"""
Data manager for the Dash dashboard.
Fetches data from the dashboard API and structures it into Pandas DataFrames.
"""
import json
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from slicing_dashboard.config import get_settings
from slicing_dashboard.scraper.http_scraper import HTTPScraper


from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock, RLock
from time import perf_counter
from typing import Any
from slicing_dashboard.processing.source_policy import SOURCE_TTL_SECONDS, serialized_source as _serialized_source


_REFRESH_SCOPE = ContextVar('dashboard_refresh_scope', default=None)
_DAILY_REPORT_FILES_LOCK = RLock()
_DAILY_PAYLOAD_LOCK = RLock()
_SOURCE_LOCKS_INIT = Lock()


def _per_refresh(function):
    """Share identical reads, including concurrent readers, within one refresh."""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapper(self, *args, **kwargs):
        scope = _REFRESH_SCOPE.get()
        if scope is None or scope['manager'] is not self:
            return function(self, *args, **kwargs)
        arguments = signature.bind(self, *args, **kwargs)
        arguments.apply_defaults()
        key = (function.__name__, tuple((name, value) for name, value in arguments.arguments.items() if name != 'self'))
        with scope['lock']:
            future = scope['reads'].get(key)
            first_reader = future is None
            if first_reader:
                future = scope['reads'][key] = Future()
            else:
                scope['memoized_hits'] += 1
        if first_reader:
            started = perf_counter()
            outcome = 'ok'
            try:
                future.set_result(function(self, *args, **kwargs))
            except BaseException as error:
                outcome = type(error).__name__
                future.set_exception(error)
                raise
            finally:
                safe_arguments = {name: value for name, value in arguments.arguments.items()
                                  if name in {'start_date', 'end_date', 'target_date', 'role', 'force_refresh', 'include_summary'}}
                with scope['lock']:
                    scope['timings'].append({'method': function.__name__, 'arguments': safe_arguments,
                                             'seconds': round(perf_counter() - started, 4), 'outcome': outcome})
        return future.result()

    return wrapper


class DataManager:

    @contextmanager
    def refresh_scope(self):
        """Reuse reads and persist once per callback; independent callbacks stay isolated."""
        existing = _REFRESH_SCOPE.get()
        if existing is not None and existing['manager'] is self:
            yield
            return
        self.refresh_shared_configuration()
        scope = {'manager': self, 'reads': {}, 'lock': Lock(), 'snapshot_dirty': False,
                 'started': perf_counter(), 'timings': [], 'memoized_hits': 0}
        token = _REFRESH_SCOPE.set(scope)
        try:
            yield
        finally:
            _REFRESH_SCOPE.reset(token)
            snapshot_started = perf_counter()
            if scope['snapshot_dirty']:
                self._save_snapshot()
            snapshot_seconds = perf_counter() - snapshot_started if scope['snapshot_dirty'] else 0.0
            if scope.get('daily_source') is not None:
                scope['daily_source'].invalidate()
            self.last_refresh_profile = {
                'captured_at': datetime.now().isoformat(),
                'elapsed_seconds': round(perf_counter() - scope['started'], 4),
                'snapshot_seconds': round(snapshot_seconds, 4),
                'memoized_hits': scope['memoized_hits'],
                'reads': sorted(scope['timings'], key=lambda timing: timing['seconds'], reverse=True),
                'database': {},
                'snapshot_persistence': {'attempted': False},
            }
            database_profile = getattr(self.db, 'last_snapshot_profile', None)
            if isinstance(database_profile, dict) and scope['snapshot_dirty']:
                self.last_refresh_profile['database'] = database_profile
            persistence = getattr(self, 'last_snapshot_save_result', None)
            if isinstance(persistence, dict) and scope['snapshot_dirty']:
                self.last_refresh_profile['snapshot_persistence'] = persistence
            try:
                from slicing_dashboard.config import DATA_DIR
                target = DATA_DIR / 'reports' / 'performance-audit' / 'last-refresh-profile.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(self.last_refresh_profile, indent=2), encoding='utf-8')
            except OSError:
                pass

    def _refresh_worker(self, function):
        """Carry the callback's read cache into explicitly created worker threads."""
        scope = _REFRESH_SCOPE.get()

        @wraps(function)
        def worker(*args, **kwargs):
            token = _REFRESH_SCOPE.set(scope)
            try:
                return function(*args, **kwargs)
            finally:
                _REFRESH_SCOPE.reset(token)

        return worker

    def invalidate_reporting_caches(self):
        """Discard mapped/derived values when the shared account mapping changes."""
        self._daily_payload_cache = {}
        self._current_queue_cache = {}
        self.invalidate_pending_review()

    def refresh_shared_configuration(self, force=False):
        """Bound mapping lag across warm workers without restarting the app.

        Keep the last valid mappings on a database outage and report failure to
        operations. Raw source caches are remapped when next rendered.
        """
        settings = getattr(self, 'settings', None)
        if settings is None:  # Lightweight/offline managers have no shared configuration.
            return True
        database = getattr(self, 'db', None)
        if database is None or database.mongo_db is None:
            return not bool(settings.mongo_uri or str(settings.database_url or '').startswith('mongodb'))
        with _SOURCE_LOCKS_INIT:
            if not hasattr(self, '_configuration_lock'):
                self._configuration_lock = RLock()
        with self._configuration_lock:
            if not force and perf_counter() - getattr(self, '_configuration_checked_at', -float('inf')) < SOURCE_TTL_SECONDS:
                return not getattr(self, '_configuration_error', None)
            self._configuration_checked_at = perf_counter()
            try:
                records = database.load_user_mappings(strict=True)
                mappings = {row['id']: {key: value for key, value in row.items() if key != '_id'} for row in records}
                periods = database.load_settlement_periods(strict=True)
                if any(not row.get('mapped_user') for row in mappings.values()):
                    raise ValueError('Invalid shared mappings')
                if mappings != getattr(self, 'user_mapping_full', {}):
                    self.user_mapping_full = mappings
                    self.user_mapping = {key: row['mapped_user'] for key, row in mappings.items()}
                    self.exempt_ids = {key for key, row in mappings.items() if row.get('mapping_type') == 'Exempt'}
                    self.invalidate_reporting_caches()
                self.settlement_periods = periods
                self._configuration_error = None
                return True
            except Exception as error:
                self._configuration_error = type(error).__name__
                return False

    def _daily_work_reader(self):
        """Keep the complete submission inventory only for the current callback."""
        from slicing_dashboard.processing.daily_work_source import DailyWorkSource
        scope = _REFRESH_SCOPE.get()
        if scope is None or scope['manager'] is not self:
            return DailyWorkSource(self.scraper, self._get_canonical_name)
        with scope['lock']:
            reader = scope.get('daily_source')
            if reader is None:
                reader = scope['daily_source'] = DailyWorkSource(
                    self.scraper, self._get_canonical_name, group_id=scope.get('group_id'))
            seed_returns = dict(scope.get('return_responses', {}))
        reader.seed_returns(seed_returns)
        return reader

    def prepare_daily_work(self, target_date):
        """Prefetch submission inventory while independent aggregates are loading."""
        return self._daily_work_reader().prepare(target_date)

    def __init__(self):
        self.settings = get_settings()
        self.scraper = HTTPScraper(self.settings)
        self._cache = {}
        self._daily_cache = {}
        self._daily_report_records = {}
        self._daily_report_lock = RLock()
        from slicing_dashboard.db import DatabaseManager
        self.db = DatabaseManager()
        
        # Load user mappings from MongoDB
        db_mappings = self.db.load_user_mappings()
        self.user_mapping_full = {m['id']: m for m in db_mappings}
        self.user_mapping = {m['id']: m['mapped_user'] for m in db_mappings}
        self.exempt_ids = {m['id'] for m in db_mappings if m.get('mapping_type') == 'Exempt'}
        
        if not self.user_mapping:
            mapping_path = Path(self.settings.user_mapping_path)
            if not mapping_path.is_absolute():
                from slicing_dashboard.config import PROJECT_ROOT
                mapping_path = PROJECT_ROOT / self.settings.user_mapping_path
            if mapping_path.exists():
                with open(mapping_path) as f:
                    self.user_mapping = json.load(f)
                    self.user_mapping_full = {k: {'id': k, 'mapped_user': v, 'mapping_type': 'Existing'} for k, v in self.user_mapping.items()}
            else:
                self.user_mapping = {}
                self.user_mapping_full = {}

        # Load settlement periods from MongoDB
        self.settlement_periods = self.db.load_settlement_periods()
        
        from slicing_dashboard.config import PROJECT_ROOT, DATA_DIR
        if not self.settlement_periods:
            settlement_path = PROJECT_ROOT / 'config' / 'settlement_history.json'
            if settlement_path.exists():
                with open(settlement_path) as f:
                    self.settlement_history = json.load(f)
            else:
                self.settlement_history = {}
        else:
            self.settlement_history = {}

        self._data = None
        self._transitions_data = None
        self._last_loaded = None

        # ── Snapshot & Server Status Tracking ────────────────────────
        self._snapshot_path = DATA_DIR / 'snapshot_cache.json'
        self.server_is_live: bool = True
        self.is_using_snapshot: bool = False
        self.last_sync_time: str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.last_sync_error: str | None = None
        self._last_heartbeat_check: datetime | None = None
        self._snapshot_payload: dict = {}

        # ── Persistent Batch Returns & Batches Master Tracking ───────
        self._batch_returns_path = DATA_DIR / 'batch_returns_history.json'
        self._batch_returns_cache: list[dict[str, Any]] = []
        self._last_batch_returns_sync: datetime | None = None

        self._batches_master_path = DATA_DIR / 'batches_master.json'
        self._batches_master_cache: dict[str, dict[str, Any]] = {}
        self._batches_master_synced_dates: set[str] = set()
        self._last_batches_sync: datetime | None = None

        # Always load master data and snapshot cache on startup
        self.load_data()
        self._load_snapshot()
        self._load_batch_returns()
        self._load_batches_master()

    def _load_snapshot(self) -> None:
        """Load cached snapshot from disk or database if available."""
        snap = None
        # 1. Try loading from local file or /tmp (Vercel)
        for p in [self._snapshot_path, Path("/tmp/snapshot_cache.json")]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        snap = json.load(f)
                        if snap:
                            break
                except Exception:
                    pass

        # 2. Try loading from database if connected (persists across Vercel serverless functions)
        try:
            db_snap = self.db.load_dashboard_snapshot()
            if db_snap and isinstance(db_snap, dict):
                snap = db_snap
        except Exception:
            pass

        if snap:
            self._snapshot_payload = snap
            if "cache" in snap and isinstance(snap["cache"], dict):
                self._cache.update(snap["cache"])
            if "daily_cache" in snap and isinstance(snap["daily_cache"], dict):
                self._daily_cache.update(snap["daily_cache"])
            if "batches_master_records" in snap and isinstance(snap["batches_master_records"], list):
                self._batches_master_cache = {
                    b["batch_id"]: b for b in snap["batches_master_records"] if isinstance(b, dict) and b.get("batch_id")
                }
            if "batch_returns_records" in snap and isinstance(snap["batch_returns_records"], list):
                self._batch_returns_cache = snap["batch_returns_records"]
            self._batches_master_synced_dates.update(snap.get('batches_master_synced_dates', []))
            self.last_sync_time = snap.get("last_sync_time", self.last_sync_time)

    def _save_snapshot(self) -> None:
        """Persist current cache to disk and database as snapshot."""
        scope = _REFRESH_SCOPE.get()
        if scope is not None and scope['manager'] is self:
            scope['snapshot_dirty'] = True
            return
        self.last_snapshot_save_result = {'attempted': True, 'local_saved': False, 'database_saved': False}
        try:
            self.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cache_copy = dict(list(self._cache.items()))
            daily_copy = {k: dict(v) if isinstance(v, dict) else v for k, v in list(self._daily_cache.items())}
            payload = {
                "last_sync_time": self.last_sync_time,
                "cache": cache_copy,
                "daily_cache": daily_copy,
                "summary_kpis": self._snapshot_payload.get("summary_kpis", {}),
                "user_breakdown_records": self._snapshot_payload.get("user_breakdown_records", []),
                "available_users": self._snapshot_payload.get("available_users", []),
                "batch_returns_records": self._batch_returns_cache,
                "batches_master_records": list(self._batches_master_cache.values()),
                "batches_master_synced_dates": sorted(getattr(self, '_batches_master_synced_dates', set())),
            }
            self._snapshot_payload = payload

            # 1. Save to disk (DATA_DIR locally, /tmp on Vercel)
            for target in [self._snapshot_path, Path("/tmp/snapshot_cache.json")]:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                    self.last_snapshot_save_result['local_saved'] = True
                    break
                except Exception:
                    continue

            # 2. Save to database if connected (for Vercel persistence across all lambdas)
            try:
                self.last_snapshot_save_result['database_saved'] = bool(self.db.save_dashboard_snapshot(payload))
            except Exception as error:
                self.last_snapshot_save_result['database_error_class'] = type(error).__name__
        except Exception as e:
            self.last_snapshot_save_result['snapshot_error_class'] = type(e).__name__
            print(f"Warning: Failed to save snapshot: {e}")

    def check_server_heartbeat(self, timeout: float = 2.5) -> bool:
        """Probe the server using the official /api/auth/heartbeat endpoint."""
        now = datetime.now()
        if (
            self._last_heartbeat_check is not None
            and (now - self._last_heartbeat_check) < timedelta(seconds=15)
        ):
            return self.server_is_live

        self._last_heartbeat_check = now
        try:
            url = f"{self.scraper._base_url}/api/auth/heartbeat"
            user_id = 6552
            if hasattr(self.scraper, "_users") and self.scraper._users:
                user_id = next(iter(self.scraper._users.keys()), 6552)

            resp = self.scraper._client.post(
                url,
                json={"user_id": user_id},
                timeout=timeout,
            )
            # 200 OK means backend is live and actively processing
            if resp.status_code == 200:
                self.server_is_live = True
                self.last_sync_error = None
                return True
            elif resp.status_code in [500, 502, 503, 504]:
                self.server_is_live = False
                self.last_sync_error = f"Gateway error ({resp.status_code})"
                return False
            else:
                # 401/403 or other status means server process is responding
                self.server_is_live = True
                return True
        except Exception as e:
            self.server_is_live = False
            self.last_sync_error = str(e)
            return False

    def get_server_status(self) -> dict:
        """Return a status dictionary for dashboard UI."""
        return {
            "is_live": self.server_is_live,
            "is_using_snapshot": self.is_using_snapshot,
            "last_sync_time": self.last_sync_time,
            "error": self.last_sync_error,
            "configuration_error": getattr(self, '_configuration_error', None),
        }

    def load_data(self) -> None:
        """Load slicing and transitions master data from local processed CSV."""
        from slicing_dashboard.config import PROJECT_ROOT
        slicing_path = PROJECT_ROOT / 'data' / 'processed' / 'slicing_master.csv'
        transitions_path = PROJECT_ROOT / 'data' / 'processed' / 'transitions_master.csv'
        if slicing_path.exists():
            try:
                self._data = pd.read_csv(slicing_path)
            except Exception:
                self._data = pd.DataFrame()
        else:
            self._data = pd.DataFrame()

        if transitions_path.exists():
            try:
                self._transitions_data = pd.read_csv(transitions_path)
            except Exception:
                self._transitions_data = None
        else:
            self._transitions_data = None
        self._last_loaded = datetime.now()

    def _get_canonical_name(self, uid: int, username: str) -> str:
        u_str = str(username) if username is not None else ""
        if u_str in getattr(self, 'exempt_ids', set()):
            return "Exempt"
        for k, v in self.user_mapping.items():
            if k.lower() == u_str.lower():
                if v == "Exempt":
                    return "Exempt"
                return v
        return u_str

    def _batch_canonical_name(self, batch: dict) -> str:
        """Resolve current mappings rather than the name saved in an old ledger."""
        username = batch.get('username')
        if username:
            return self._get_canonical_name(batch.get('assignee_id', 0), username)
        return batch.get('canonical_user', '')

    def get_unassigned_users(self, force_refresh: bool = False, force_refresh_users: bool = False, **kwargs) -> list[str]:
        """Find all usernames/IDs that appear in API or batches or records but are not in user_mapping."""
        force_refresh = force_refresh or force_refresh_users
        candidates = set()

        # 1. Scraper users from API
        try:
            if not self.scraper._users or force_refresh:
                self.scraper._fetch_users(force=force_refresh)
            for u in self.scraper._users.values():
                uname = u.get("username")
                gname = u.get("group_name")
                if uname:
                    if gname == "SSHD TECHNOLOGIES" or uname.upper().startswith("SSHD"):
                        candidates.add(uname)
        except Exception:
            pass

        # 2. Batches master cache
        for b in self._batches_master_cache.values():
            u = b.get("username")
            if u:
                candidates.add(u)

        # 3. Snapshot cache
        if hasattr(self, "_snapshot_payload") and isinstance(self._snapshot_payload, dict):
            for b in self._snapshot_payload.get("batches_master_records", []):
                u = b.get("username")
                if u:
                    candidates.add(u)
            for rec in self._snapshot_payload.get("user_breakdown_records", []):
                u = rec.get("User")
                if u:
                    candidates.add(u)

        # 4. Local master CSV if present
        if self._data is not None and not self._data.empty and "user_name" in self._data.columns:
            for u in self._data["user_name"].dropna().unique():
                candidates.add(str(u))

        ignore_names = {"All Slicers", "TOTAL", "Admin", "Test", "Dep", "user-None", "", "(unassigned)", "None"}
        ignore_names.update(self.user_mapping.values())
        mapped_keys = set(self.user_mapping_full.keys())

        unassigned = sorted([
            u for u in candidates
            if u not in ignore_names and u not in mapped_keys and not u.startswith("user-None")
        ])
        return unassigned



    @_per_refresh
    @_serialized_source
    def fetch_dashboard_data(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> dict:
        """Fetches overview dashboard data with safe snapshot fallback."""
        cache_key = f'{start_date}_{end_date}'
        clock = getattr(self, '_source_cache_times', {}).get(cache_key, float('-inf'))
        if not force_refresh and cache_key in self._cache and perf_counter() - clock < SOURCE_TTL_SECONDS:
            return self._cache[cache_key]

        try:
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError("Login failed")
            if not self.scraper._users:
                self.scraper._fetch_users()
            overview_url = f'{self.scraper._base_url}/api/dashboard/overview'
            params = {'mode': 'slice', 'start_date': start_date, 'end_date': end_date}
            response = self.scraper._client.get(overview_url, params=params, timeout=5.0)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('metrics'), dict):
                raise ValueError('Invalid dashboard overview')
            self._cache[cache_key] = data
            if not hasattr(self, '_source_cache_times'):
                self._source_cache_times = {}
            self._source_cache_times[cache_key] = perf_counter()
            scope = _REFRESH_SCOPE.get()
            if scope is not None and scope['manager'] is self:
                api_scope = data.get('scope', {})
                if api_scope.get('group_id') is not None:
                    scope['group_id'] = api_scope['group_id']
                if isinstance(api_scope.get('member_ids'), list):
                    scope['member_ids'] = set(api_scope['member_ids'])
            self.server_is_live = True
            self.is_using_snapshot = False
            self.last_sync_error = None
            self._save_snapshot()
            return data
        except Exception as e:
            self.server_is_live = False
            self.is_using_snapshot = True
            self.last_sync_error = str(e)
            getattr(self, '_source_cache_times', {}).pop(cache_key, None)
            saved = self._cache.get(cache_key, {"metrics": {}, "breakdowns": {}})
            return {**saved, '_source_error': type(e).__name__}

    @_per_refresh
    @_serialized_source
    def get_assignable_pool(self, force_refresh=False):
        """Reuse only a recent complete current inventory; errors aren't zeros."""
        from copy import deepcopy
        from slicing_dashboard.processing.assignable_pool import fetch_inventory
        cached = getattr(self, '_assignable_pool_cache', None)
        if cached and not force_refresh and perf_counter() - cached[0] < SOURCE_TTL_SECONDS:
            return deepcopy(cached[1])
        self._assignable_pool_cache = None
        try:
            capture = fetch_inventory(self.scraper)
            self._assignable_pool_cache = (perf_counter(), capture)
            return deepcopy(capture)
        except Exception as error:
            return {'available': False, 'duration_seconds': None, 'task_count': None,
                    'captured_at': None, 'error': type(error).__name__}

    def get_summary_kpis(self, start_date: str, end_date: str,
        selected_users: (list[str] | None) = None,
        force_refresh: bool = False) -> dict:
        """Fetch summary KPIs matching the official Slice Data Overview platform."""
        pool = self.get_assignable_pool(force_refresh)
        pool_metrics = {'assignable_duration': pool['duration_seconds'],
                        'assignable_count': pool['task_count'],
                        'assignable_error': pool['error'],
                        'assignable_captured_at': pool['captured_at']}
        try:
            curr_summary, curr_items = self.fetch_annotator_efficiency(
                start_date=start_date, end_date=end_date, role=2, force_refresh=force_refresh
            )
            if curr_summary.get('_source_error'):
                return {'_source_error': curr_summary['_source_error']}
            from datetime import datetime, timedelta
            fmt = '%Y-%m-%d'
            dt_start = datetime.strptime(start_date, fmt)
            dt_end = datetime.strptime(end_date, fmt)
            diff = dt_end - dt_start
            prev_end = dt_start - timedelta(days=1)
            prev_start = prev_end - diff
            prev_summary, prev_items = self.fetch_annotator_efficiency(
                start_date=prev_start.strftime(fmt), end_date=prev_end.strftime(fmt), role=2, force_refresh=force_refresh
            )
            if prev_summary.get('_source_error'):
                return {'_source_error': prev_summary['_source_error']}

            def calc_pct(curr, prev):
                if not prev:
                    return 0.0
                return (curr - prev) / prev * 100.0

            raw_data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
            metrics = raw_data.get('metrics', {})

            if selected_users:
                c_dur = 0.0
                c_cnt = 0
                t_cnt = 0
                rew_dur = 0.0
                err_dur = 0.0
                lead_dur = 0.0
                aud_dur = 0.0
                adm_dur = 0.0
                for it in curr_items:
                    raw_u = it.get('username', '')
                    canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                    if canonical in selected_users:
                        c_dur += float(it.get('completed_duration_seconds', 0) or 0)
                        c_cnt += int(it.get('completed_count', 0) or 0)
                        t_cnt += int(it.get('total_count', 0) or 0)
                        rew_dur += float(it.get('rework_duration_seconds', 0) or 0)
                        err_dur += float(it.get('error_review_duration_seconds', 0) or 0)
                        lead_dur += float(it.get('leader_review_duration_seconds', 0) or 0)
                        aud_dur += float(it.get('auditor_review_duration_seconds', 0) or 0)
                        adm_dur += float(it.get('admin_review_duration_seconds', 0) or 0)

                p_dur = 0.0
                p_cnt = 0
                p_rew_dur = 0.0
                p_err_dur = 0.0
                for it in prev_items:
                    raw_u = it.get('username', '')
                    canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                    if canonical in selected_users:
                        p_dur += float(it.get('completed_duration_seconds', 0) or 0)
                        p_cnt += int(it.get('completed_count', 0) or 0)
                        p_rew_dur += float(it.get('rework_duration_seconds', 0) or 0)
                        p_err_dur += float(it.get('error_review_duration_seconds', 0) or 0)

                return {
                    'total_approved_duration': c_dur,
                    'prev_approved_duration': p_dur,
                    'approved_pct': calc_pct(c_dur, p_dur),
                    'total_error_duration': err_dur,
                    'prev_error_duration': p_err_dur,
                    'error_pct': calc_pct(err_dur, p_err_dur),
                    'rework_duration': rew_dur,
                    'prev_rework_duration': p_rew_dur,
                    'rework_pct': calc_pct(rew_dur, p_rew_dur),
                    'total_tasks': t_cnt,
                    'completed_tasks': c_cnt,
                    'prev_completed_tasks': p_cnt,
                    'completed_pct': calc_pct(c_cnt, p_cnt),
                    **pool_metrics,
                    'total_backlog_duration': metrics.get('slice_backlog_duration_seconds', 0),
                    'total_pending_duration': lead_dur + aud_dur + adm_dur,
                    'leader_review_duration': lead_dur,
                    'auditor_review_duration': aud_dur,
                    'admin_review_duration': adm_dur,
                }

            lead_dur = float(curr_summary.get('leader_review_duration_seconds', 0) or 0)
            aud_dur = float(curr_summary.get('auditor_review_duration_seconds', 0) or 0)
            adm_dur = float(curr_summary.get('admin_review_duration_seconds', 0) or 0)
            curr_comp_dur = float(curr_summary.get('completed_duration_seconds', 0) or 0)
            prev_comp_dur = float(prev_summary.get('completed_duration_seconds', 0) or 0)
            curr_err_dur = float(curr_summary.get('error_review_duration_seconds', 0) or 0)
            prev_err_dur = float(prev_summary.get('error_review_duration_seconds', 0) or 0)
            curr_rew_dur = float(curr_summary.get('rework_duration_seconds', 0) or 0)
            prev_rew_dur = float(prev_summary.get('rework_duration_seconds', 0) or 0)
            curr_comp_cnt = int(curr_summary.get('completed_count', 0) or 0)
            prev_comp_cnt = int(prev_summary.get('completed_count', 0) or 0)

            return {
                'total_approved_duration': curr_comp_dur,
                'prev_approved_duration': prev_comp_dur,
                'approved_pct': calc_pct(curr_comp_dur, prev_comp_dur),
                'total_error_duration': curr_err_dur,
                'prev_error_duration': prev_err_dur,
                'error_pct': calc_pct(curr_err_dur, prev_err_dur),
                'rework_duration': curr_rew_dur,
                'prev_rework_duration': prev_rew_dur,
                'rework_pct': calc_pct(curr_rew_dur, prev_rew_dur),
                'total_tasks': int(curr_summary.get('total_count', 0) or 0),
                'completed_tasks': curr_comp_cnt,
                'prev_completed_tasks': prev_comp_cnt,
                'completed_pct': calc_pct(curr_comp_cnt, prev_comp_cnt),
                **pool_metrics,
                'total_backlog_duration': metrics.get('slice_backlog_duration_seconds', 0),
                'total_pending_duration': lead_dur + aud_dur + adm_dur,
                'leader_review_duration': lead_dur,
                'auditor_review_duration': aud_dur,
                'admin_review_duration': adm_dur,
            }
        except Exception as e:
            print(f"Warning: Failed to fetch summary KPIs from efficiency API: {e}. Falling back to overview.")
            data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
            metrics = data.get('metrics', {})
            return {
                'total_approved_duration': metrics.get('slice_completed_duration_seconds', 0),
                'prev_approved_duration': 0.0,
                'approved_pct': 0.0,
                'total_error_duration': metrics.get('error_video_output_duration_seconds', 0),
                'prev_error_duration': 0.0,
                'error_pct': 0.0,
                'rework_duration': metrics.get('overview_slice_rework_submitted_duration_seconds', 0),
                'prev_rework_duration': 0.0,
                'rework_pct': 0.0,
                'total_tasks': metrics.get('total_tasks', 0),
                'completed_tasks': metrics.get('slice_completed_count', 0),
                'prev_completed_tasks': 0,
                'completed_pct': 0.0,
                **pool_metrics,
                'total_backlog_duration': metrics.get('slice_backlog_duration_seconds', 0),
                'total_pending_duration': metrics.get('review_pending_duration_seconds', 0),
                'leader_review_duration': 0.0,
                'auditor_review_duration': 0.0,
                'admin_review_duration': 0.0,
            }

    def get_user_breakdown_df(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> pd.DataFrame:
        try:
            summary, items = self.fetch_annotator_efficiency(
                start_date=start_date,
                end_date=end_date,
                role=2,
                force_refresh=force_refresh, include_summary=False,
            )
            if summary.get('_source_error'):
                frame = pd.DataFrame()
                frame.attrs['chart_error'] = 'Completion data unavailable. Refresh to retry.'
                return frame
            canonical_stats: dict[str, dict[str, Any]] = {}
            for it in items:
                raw_u = it.get('username', '')
                canonical = self._get_reporting_name(it.get('user_id'), raw_u)
                if canonical in ['Admin', 'Test', 'Dep', 'Exempt', 'user-None', '', '(unassigned)']:
                    continue
                if canonical not in canonical_stats:
                    canonical_stats[canonical] = {
                        'Completed Tasks': 0,
                        'Completed Duration': 0.0,
                        'Submitted Tasks': 0,
                        'Submitted Duration': 0.0,
                        'Error Count': 0,
                        'Total Duration': 0.0,
                        'Rework Duration': 0.0,
                        'Rework Count': 0,
                    }
                canonical_stats[canonical]['Completed Tasks'] += int(it.get('completed_count', 0) or 0)
                from slicing_dashboard.processing.pending_review import number
                canonical_stats[canonical]['Completed Duration'] += number(it.get('completed_duration_seconds'))
                canonical_stats[canonical]['Submitted Tasks'] += int(it.get('submitted_count', 0) or 0)
                canonical_stats[canonical]['Submitted Duration'] += float(it.get('submitted_duration_seconds', 0) or 0.0)
                canonical_stats[canonical]['Error Count'] += int(it.get('error_review_count', 0) or 0)
                canonical_stats[canonical]['Total Duration'] += float(it.get('total_duration_seconds', 0) or 0.0)
                canonical_stats[canonical]['Rework Duration'] += float(it.get('rework_duration_seconds', 0) or 0.0)
                canonical_stats[canonical]['Rework Count'] += int(it.get('rework_count', 0) or 0)

            records = []
            for user, st in sorted(canonical_stats.items()):
                comp_dur = st['Completed Duration']
                subm_dur = st['Submitted Duration']
                rew_dur = st['Rework Duration']
                worked_dur = max(comp_dur, subm_dur, st['Total Duration'], rew_dur)
                new_work_dur = max(0.0, worked_dur - rew_dur)
                tot_tasks = st['Completed Tasks'] + st['Submitted Tasks'] + st['Rework Count']
                tot_dur = worked_dur
                # Exclude users who have no work or rework recorded in this period
                if tot_tasks <= 0 and tot_dur <= 0 and rew_dur <= 0:
                    continue
                records.append({
                    'User': user,
                    'Completed Tasks': st['Completed Tasks'],
                    'Completed Duration': comp_dur,
                    'Submitted Tasks': st['Submitted Tasks'],
                    'Submitted Duration': subm_dur,
                    'Error Count': st['Error Count'],
                    'Total Duration': tot_dur,
                    'Rework Duration': rew_dur,
                    'Rework Count': st['Rework Count'],
                    'New Work Duration': new_work_dur,
                })
            return pd.DataFrame(records)
        except Exception as e:
            frame = pd.DataFrame()
            frame.attrs['chart_error'] = 'Completion data unavailable. Refresh to retry.'
            return frame

    def get_cumulative_df(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> pd.DataFrame:
        """Fetch cumulative performance day-by-day in real time directly from the API."""
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        all_dates = [(start + timedelta(days=x)).strftime('%Y-%m-%d') for x in range((end - start).days + 1)]
        today_str = datetime.now().strftime('%Y-%m-%d')

        # If server is offline, populate missing dates from local master CSV and avoid slow network timeouts
        if not self.server_is_live and self._data is not None and not self._data.empty:
            try:
                c_df = self._data[(self._data["is_completed"] == True) & (self._data["completed_date"].isin(all_dates))].copy()
                if not c_df.empty:
                    c_df["canonical_user"] = c_df.apply(lambda r: self._get_canonical_name(r.get("user_id"), r.get("user_name")), axis=1)
                    for (dt, u), grp in c_df.groupby(["completed_date", "canonical_user"]):
                        if u not in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                            if dt not in self._daily_cache:
                                self._daily_cache[dt] = {}
                            self._daily_cache[dt][u] = float(grp["duration_seconds"].sum())
            except Exception:
                pass

        # Determine dates that need fetching
        dates_to_fetch = []
        if self.server_is_live:
            for d in all_dates:
                if d == today_str:
                    if force_refresh or d not in self._daily_cache:
                        dates_to_fetch.append(d)
                elif d not in self._daily_cache or force_refresh:
                    dates_to_fetch.append(d)

        if dates_to_fetch:
            def fetch_single_day(dt_str):
                try:
                    summary, items = self.fetch_annotator_efficiency(
                        start_date=dt_str,
                        end_date=dt_str,
                        role=2,
                        force_refresh=force_refresh, include_summary=False,
                    )
                    day_users = {}
                    for it in items:
                        canonical = self._get_canonical_name(it.get('user_id'), it.get('username'))
                        if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                            continue
                        completed_dur = float(it.get('completed_duration_seconds', 0) or 0.0)
                        day_users[canonical] = day_users.get(canonical, 0.0) + completed_dur
                    return dt_str, day_users
                except Exception:
                    return dt_str, {}

            workers = min(len(dates_to_fetch), 5)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = dict(executor.map(self._refresh_worker(fetch_single_day), dates_to_fetch))

            for d, users in results.items():
                self._daily_cache[d] = users

        daily_dict = {d: self._daily_cache.get(d, {}) for d in all_dates}
        if not daily_dict:
            return pd.DataFrame()

        rows = []
        for dt, users in daily_dict.items():
            row = {'Date': dt}
            row.update(users)
            rows.append(row)
        pivot = pd.DataFrame(rows).set_index('Date').fillna(0)
        pivot = pivot.reindex(all_dates, fill_value=0)

        cumulative_pivot = pivot.cumsum()
        cumulative_pivot.index.name = 'Date'
        return cumulative_pivot.reset_index()

    def _load_batch_returns(self) -> None:
        """Load persistent batch return history from JSON dataset or snapshot cache."""
        records = []
        for p in [self._batch_returns_path, Path("/tmp/batch_returns_history.json")]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "records" in data:
                            records = data["records"]
                        elif isinstance(data, list):
                            records = data
                        if records:
                            break
                except Exception:
                    pass

        if not records and "batch_returns_records" in self._snapshot_payload:
            records = self._snapshot_payload.get("batch_returns_records", [])

        self._batch_returns_cache = records

    def _save_batch_returns(self, records: list[dict[str, Any]]) -> None:
        """Persist batch return history dataset to disk and snapshot cache."""
        self._batch_returns_cache = records
        self._snapshot_payload["batch_returns_records"] = records
        payload = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_records": len(records),
            "records": records,
        }
        for target in [self._batch_returns_path, Path("/tmp/batch_returns_history.json")]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                break
            except Exception:
                continue

    @_per_refresh
    def sync_batch_returns(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Sync live batch return history from upstream API and merge into persistent dataset.
        
        Uses an append-only merge strategy so historical batch return data is permanently
        preserved even if upstream endpoints purge older history.
        """
        now = datetime.now()
        if (not force_refresh and self._last_batch_returns_sync is not None
                and (now - self._last_batch_returns_sync).total_seconds() < SOURCE_TTL_SECONDS
                and not getattr(self, '_returns_source_error', None)):
            return self._batch_returns_cache

        try:
            self._returns_source_error = None
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError('Return history login failed')

            if not self.scraper._users:
                self.scraper._fetch_users()

            url = f"{self.scraper._base_url}/api/requests/batch-return-history"
            existing_map = {r.get("event_id"): r for r in self._batch_returns_cache if r.get("event_id")}
            scope = _REFRESH_SCOPE.get()
            if scope is not None and scope['manager'] is self:
                scope.setdefault('return_responses', {})
            else:
                scope = None

            def fetch_user_returns(u_tuple):
                uid, uinfo = u_tuple
                uname = uinfo.get("username", "") if isinstance(uinfo, dict) else str(uinfo)
                canonical = self._get_canonical_name(uid, uname)
                if canonical in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"]:
                    return []
                try:
                    r = self.scraper._client.get(
                        url,
                        params={"workflow_type": "slice", "limit": 100},
                        headers={"x-user-id": str(uid)},
                        timeout=4.0,
                    )
                    if r.status_code == 200:
                        payload = r.json()
                        items = payload.get("data", [])
                        if not isinstance(items, list):
                            raise ValueError('Invalid return history')
                        # This endpoint is bounded. Do not claim an exhaustive
                        # distribution when the response signals more records.
                        if payload.get('has_more') or payload.get('meta', {}).get('has_more'):
                            raise ValueError('Incomplete return history')
                        if scope is not None:
                            with scope['lock']:
                                scope['return_responses'][uid] = payload
                        user_records = []
                        for it in items:
                            b_id = str(it.get("batch_id", "") or "")
                            ret_at = str(it.get("returned_at", "") or "")
                            leg_b = it.get("legacy_batch_number")
                            reas = str(it.get("reason", "") or "")
                            evt_id = f"{uid}_{b_id}_{ret_at}"
                            user_records.append({
                                "event_id": evt_id,
                                "user_id": uid,
                                "username": uname,
                                "canonical_user": canonical,
                                "batch_id": b_id,
                                "legacy_batch_number": leg_b,
                                "reason": reas,
                                "returned_at": ret_at,
                            })
                        return user_records
                except Exception:
                    self._returns_source_error = 'Return history unavailable'
                    return []
                self._returns_source_error = 'Return history unavailable'
                return []

            users_list = list(self.scraper._users.items())
            if scope is not None and scope.get('member_ids'):
                users_list = [(uid, info) for uid, info in users_list if uid in scope['member_ids']]
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = executor.map(fetch_user_returns, users_list)
                for res in results:
                    for rec in res:
                        existing_map[rec["event_id"]] = rec

            merged_records = list(existing_map.values())
            merged_records.sort(key=lambda x: x.get("returned_at", ""), reverse=True)
            self._save_batch_returns(merged_records)
            self._last_batch_returns_sync = now
            return merged_records
        except Exception as e:
            self._returns_source_error = type(e).__name__
            print(f"Warning: Failed to sync batch return history: {e}")
            return self._batch_returns_cache

    def _load_batches_master(self) -> None:
        """Load persistent batches master ledger from JSON dataset or snapshot cache."""
        records = []
        for p in [self._batches_master_path, Path("/tmp/batches_master.json")]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "batches" in data:
                            records = data["batches"]
                            self._batches_master_synced_dates.update(data.get('fetched_dates', []))
                        elif isinstance(data, list):
                            records = data
                        if records:
                            break
                except Exception:
                    pass

        if not records and "batches_master_records" in self._snapshot_payload:
            records = self._snapshot_payload.get("batches_master_records", [])

        self._batches_master_cache = {
            b["batch_id"]: b for b in records if isinstance(b, dict) and b.get("batch_id")
        }
        self._batches_master_synced_dates.update(
            batch['batch_date'] for batch in self._batches_master_cache.values() if batch.get('batch_date'))

    def _save_batches_master(self, batches_dict: dict[str, dict[str, Any]]) -> None:
        """Persist batches master ledger to disk and snapshot cache."""
        self._batches_master_cache = batches_dict
        batches_list = list(batches_dict.values())
        batches_list.sort(key=lambda x: (x.get("batch_date", ""), x.get("batch_id", "")))
        self._snapshot_payload["batches_master_records"] = batches_list

        payload = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_batches": len(batches_list),
            "batches": batches_list,
            "fetched_dates": sorted(getattr(self, '_batches_master_synced_dates', set())),
        }
        for target in [self._batches_master_path, Path("/tmp/batches_master.json")]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                break
            except Exception:
                continue

    @_per_refresh
    def sync_batches_master(
        self, start_date: str, end_date: str, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """Sync ground-truth batches from upstream API incrementally into persistent master ledger.
        
        Uses an append-only merge strategy with exact durations and return counts so historical
        batches are permanently preserved even if upstream endpoints purge older history.
        """
        now = datetime.now()

        synced_dates = getattr(self, '_batches_master_synced_dates', set())
        synced_dates.update(batch['batch_date'] for batch in self._batches_master_cache.values() if batch.get('batch_date'))
        self._batches_master_synced_dates = synced_dates
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        requested_dates = {(start + timedelta(days=index)).strftime('%Y-%m-%d')
                           for index in range((end - start).days + 1)}
        recent = (self._last_batches_sync is not None and (now - self._last_batches_sync).total_seconds() < SOURCE_TTL_SECONDS)
        if not force_refresh and requested_dates <= synced_dates and recent and not getattr(self, '_batch_source_error', None):
            return self._batches_master_cache

        try:
            self._batch_source_error = None
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError('Batch history login failed')

            if not self.scraper._users:
                self.scraper._fetch_users()

            # Ensure batch return history is up-to-date to join return counts
            returns = self.sync_batch_returns(force_refresh=force_refresh)
            returns_by_batch_id = defaultdict(int)
            for r in returns:
                bid = r.get("batch_id")
                if bid:
                    returns_by_batch_id[bid] += 1

            # Determine which dates in [start_date, end_date] require fetching
            from datetime import datetime as dt_cls
            fmt = "%Y-%m-%d"
            s_dt = dt_cls.strptime(start_date, fmt)
            e_dt = dt_cls.strptime(end_date, fmt)

            cached_dates = set(synced_dates)

            dates_to_fetch = []
            cur = s_dt
            while cur <= e_dt:
                d_str = cur.strftime(fmt)
                # Display interactions reuse covered dates. Explicit Refresh
                # still reloads the entire selected range, including history.
                if force_refresh or not recent or d_str not in cached_dates:
                    dates_to_fetch.append(d_str)
                cur += timedelta(days=1)

            if not dates_to_fetch:
                return self._batches_master_cache

            def fetch_day_batches(day_str):
                try:
                    r = self.scraper._client.get(
                        f"{self.scraper._base_url}/api/dashboard/batches",
                        params={"start_date": day_str, "end_date": day_str, "workflow_type": "slice"},
                        timeout=8.0,
                    )
                    if r.status_code == 200:
                        return day_str, r.json().get("breakdowns", {}).get("batches", [])
                except Exception as e:
                    print(f"Error fetching batches for {day_str}: {e}")
                return day_str, None

            with ThreadPoolExecutor(max_workers=min(10, max(1, len(dates_to_fetch)))) as ex:
                results = list(ex.map(fetch_day_batches, dates_to_fetch))

            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            updated = False
            for day_str, b_list in results:
                if b_list is None:
                    self._batch_source_error = 'Batch history incomplete'
                    continue
                self._batches_master_synced_dates.add(day_str)
                for b in b_list:
                    bid = b.get("batch_id")
                    if not bid:
                        continue
                    uid = b.get("assignee_id")
                    uinfo = self.scraper._users.get(uid, {}) if hasattr(self.scraper, "_users") else {}
                    uname = uinfo.get("username", "") if isinstance(uinfo, dict) else str(uinfo)
                    canon = self._get_canonical_name(uid, uname)

                    dur_sec = float(b.get("total_duration_seconds", 0.0) or 0.0)
                    dur_hrs = round(dur_sec / 3600.0, 4)
                    ret_cnt = returns_by_batch_id.get(bid, 0)

                    self._batches_master_cache[bid] = {
                        "batch_id": bid,
                        "batch_date": day_str,
                        "assigned_at": b.get('assigned_at'),
                        "legacy_batch_number": b.get('legacy_batch_number'),
                        "assignee_id": uid,
                        "username": uname,
                        "canonical_user": canon,
                        "status": b.get("status", ""),
                        "total_duration_seconds": dur_sec,
                        "duration_hours": dur_hrs,
                        "task_count": int(b.get("task_count", 0) or 0),
                        "production_task_count": int(b.get("production_task_count", 0) or 0),
                        "benchmark_count": int(b.get("benchmark_count", 0) or 0),
                        "completed_count": int(b.get("completed_count", 0) or 0),
                        "rework_count": int(b.get("rework_count", 0) or 0),
                        "pending_review_count": int(b.get("pending_review_count", 0) or 0),
                        "return_count": ret_cnt,
                        "last_updated": now_str,
                    }
                    updated = True

            if updated or force_refresh or self._batches_master_synced_dates != cached_dates:
                self._save_batches_master(self._batches_master_cache)
                self._last_batches_sync = now
                self._save_snapshot()

            history = self.__dict__.get('_workflow_history_service')
            if updated and history is not None:
                try:
                    history.capture_batches()
                except Exception:
                    # Preserve the successful batch refresh. Workflow storage
                    # failures remain visible/retryable in the shared inbox.
                    history.last_sync = None

            return self._batches_master_cache
        except Exception as e:
            self._batch_source_error = type(e).__name__
            print(f"Warning: Failed to sync batches master: {e}")
            return self._batches_master_cache

    def get_batch_rework_ratio_df(self, start_date: str, end_date: str, force_refresh: bool = False) -> pd.DataFrame:
        """Recorded returns per submitted batch; never infer batches from task hours."""
        from slicing_dashboard.processing.batch_ratios import batch_ratios
        try:
            self.sync_batches_master(start_date, end_date, force_refresh=force_refresh)
            returns = self.sync_batch_returns(force_refresh=force_refresh)
            if getattr(self, '_batch_source_error', None) or getattr(self, '_returns_source_error', None):
                raise ConnectionError('Batch history could not be verified')
            return batch_ratios(self._batches_master_cache.values(), returns, start_date, end_date,
                                self._batch_canonical_name)
        except Exception:
            frame = pd.DataFrame()
            frame.attrs['chart_error'] = 'Batch history unavailable. Refresh to retry.'
            return frame

    @_per_refresh
    def _load_daily_report_records(self, start_date, end_date):
        """Load only saved dated evidence, including legacy verified daily audits."""
        from slicing_dashboard.management.periods import today_iso
        from slicing_dashboard.config import DATA_DIR
        from slicing_dashboard.reporting.dashboard_reports import date_range, merge_daily_reports

        if not hasattr(self, '_daily_report_records'):
            self._daily_report_records = {}
        if not hasattr(self, '_daily_report_lock'):
            self._daily_report_lock = RLock()
        with self._daily_report_lock:
            scope = _REFRESH_SCOPE.get()
            ranges = scope.setdefault('daily_report_ranges', []) if scope is not None and scope['manager'] is self else []
            if any(start <= start_date and end_date <= end for start, end in ranges):
                return {day: record for day, record in self._daily_report_records.items()
                        if start_date <= day <= end_date}
            database = getattr(self, 'db', None)
            if database is not None:
                try:
                    for record in database.load_daily_work_reports(start_date, end_date):
                        day = record.get('date', '')
                        if start_date <= day <= end_date:
                            prior = self._daily_report_records.get(day)
                            self._daily_report_records[day] = merge_daily_reports(prior, record) if prior and 'tasks' in record else record
                except Exception as error:
                    self._daily_report_load_error = str(error)
            count = (datetime.fromisoformat(end_date) - datetime.fromisoformat(start_date)).days + 1
            for day in date_range(end_date, count):
                if day in self._daily_report_records:
                    continue
                saved_path = DATA_DIR / 'reports' / 'daily-work' / f'{day}.json'
                audit_path = DATA_DIR / 'reports' / f'daily-audit-{day}' / 'verified-submissions.json'
                for path in (saved_path, audit_path):
                    if not path.exists():
                        continue
                    try:
                        saved = json.loads(path.read_text(encoding='utf-8'))
                        if (saved.get('metadata', {}).get('target_date') != day
                                or saved.get('metadata', {}).get('available') is False):
                            continue
                        record = {'date': day, 'rows': saved.get('rows', []),
                                  'metadata': {**saved['metadata'], 'available': True,
                                               'coverage': 'observed', 'observed_on': today_iso(),
                                               'closed': day < today_iso()}}
                        if 'tasks' in saved:
                            record['tasks'] = saved['tasks']
                            record = merge_daily_reports(None, record)
                        if path == audit_path:
                            record['metadata']['capture_kind'] = 'verified_audit_import'
                            self._persist_daily_report(record)
                        self._daily_report_records[day] = record
                        break
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
            ranges.append((start_date, end_date))
            return {day: record for day, record in self._daily_report_records.items()
                    if start_date <= day <= end_date}

    def _persist_daily_report(self, record):
        """Persist dated observations independently of the global dashboard snapshot."""
        from slicing_dashboard.config import DATA_DIR
        from slicing_dashboard.reporting.dashboard_reports import merge_daily_reports
        from uuid import uuid4

        persisted = False
        destination = DATA_DIR / 'reports' / 'daily-work' / f"{record['date']}.json"
        temporary = destination.with_suffix(f'.{uuid4().hex}.tmp')
        with _DAILY_REPORT_FILES_LOCK:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    prior = json.loads(destination.read_text(encoding='utf-8'))
                    merged = merge_daily_reports(prior, record)
                    record.clear()
                    record.update(merged)
                record['metadata'].pop('persistence_error', None)
                temporary.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
                temporary.replace(destination)
                persisted = True
            except (OSError, ValueError, KeyError) as error:
                self._daily_report_save_error = str(error)
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        database = getattr(self, 'db', None)
        if database is not None:
            try:
                persisted = bool(database.save_daily_work_report(record)) or persisted
            except Exception as error:
                self._daily_report_save_error = str(error)
        return persisted

    def get_daily_report_data(self, report_date=None, force_refresh=False):
        """Share a recent capture across routes; explicit Refresh always reads live."""
        from copy import deepcopy
        from datetime import timezone
        from slicing_dashboard.management.periods import today_iso
        from slicing_dashboard.reporting.dashboard_reports import get_daily_report_data
        self.refresh_shared_configuration(force=force_refresh)
        report_date = report_date or today_iso()
        # One capture serves simultaneous dashboard/report callbacks. Mapping
        # edits change the key so cached canonical names cannot survive them.
        key = (report_date, today_iso(), json.dumps(getattr(self, 'user_mapping_full', {}), sort_keys=True))
        with _DAILY_PAYLOAD_LOCK:
            cache = getattr(self, '_daily_payload_cache', {})
            entry = cache.get(key)
            if not force_refresh and entry and perf_counter() - entry[0] < SOURCE_TTL_SECONDS:
                return deepcopy(entry[1])
            yesterday = (datetime.strptime(today_iso(), '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            refresh_live = force_refresh or report_date in (today_iso(), yesterday)
            if not force_refresh and report_date == today_iso() and hasattr(self, 'db'):
                # A cold server worker can reuse a recent durable capture too.
                # Old, failed or unverified captures still require a live scan.
                record = self._load_daily_report_records(report_date, report_date).get(report_date, {})
                metadata = record.get('metadata', {})
                try:
                    captured = datetime.fromisoformat(metadata.get('captured_at', '').replace('Z', '+00:00'))
                    age = (datetime.now(timezone.utc) - captured).total_seconds() if captured.tzinfo else None
                    if (metadata.get('available') and age is not None and 0 <= age < SOURCE_TTL_SECONDS
                            and not any(metadata.get(key) for key in ('error', 'is_snapshot', 'persistence_error'))):
                        refresh_live = False
                except (ValueError, TypeError, AttributeError):
                    pass
            payload = get_daily_report_data(self, report_date, force_refresh=refresh_live)
            cache[key] = (perf_counter(), deepcopy(payload))
            self._daily_payload_cache = {k: v for k, v in cache.items() if perf_counter() - v[0] < SOURCE_TTL_SECONDS}
            return payload

    def _daily_report_start_date(self, end_date):
        """Find retained history bounds without scanning arbitrary calendar years."""
        from slicing_dashboard.config import DATA_DIR
        dates = set(getattr(self, '_daily_report_records', {}))
        root = DATA_DIR / 'reports'
        dates.update(path.stem for path in (root / 'daily-work').glob('*.json'))
        dates.update(path.parent.name.removeprefix('daily-audit-')
                     for path in root.glob('daily-audit-*/verified-submissions.json'))
        database = getattr(self, 'db', None)
        if database is not None:
            earliest = database.daily_work_start_date(end_date)
            if earliest:
                dates.add(earliest)
        valid = []
        for value in dates:
            try:
                if datetime.strptime(value, '%Y-%m-%d').date().isoformat() == value and value <= end_date:
                    valid.append(value)
            except (ValueError, TypeError):
                continue
        return min(valid) if valid else None

    def get_user_report_data(self, report_date=None, force_refresh=False):
        from slicing_dashboard.reporting.user_reports import get_user_report_data
        return get_user_report_data(self, report_date, force_refresh)

    def _workflow_history(self):
        """Lazy durable service: never silently fork a configured database."""
        from slicing_dashboard.processing.workflow_history import WorkflowHistory, LOCK
        with LOCK:
            if not hasattr(self, '_workflow_history_service'):
                settings = get_settings()
                if ((settings.mongo_uri or settings.database_url)
                        and getattr(self.db, 'mongo_db', None) is None and getattr(self.db, 'engine', None) is None):
                    raise ConnectionError('Configured workflow database unavailable')
                self._workflow_history_service = WorkflowHistory(self)
            return self._workflow_history_service

    def get_workflow_data(self, force_refresh=False):
        self.refresh_shared_configuration(force=force_refresh)
        history = self._workflow_history()
        history.sync(force=force_refresh)
        checkpoints = [row for row in history.store.records('checkpoint') if row.get('instance') == history.instance]
        _, synthetic = history.store.query('event', history.instance, {'provenance': 'Synthetic'})
        _, total = history.store.query('event', history.instance)
        from slicing_dashboard.reporting.approval_reports import approval_records
        approvals = approval_records(
            (row for row in history.store.records('event') if row.get('instance') == history.instance),
            self._batches_master_cache.values(), checkpoints)
        return {'inbox': history.notifications(), 'storage': history.store.storage_label,
                'checkpoints': checkpoints, 'synthetic': synthetic, 'events': total,
                'approvals': approvals,
                'members': sorted((set(self.user_mapping.values()) | set(history.store.members(history.instance)))
                                  - {'Admin', 'Test', 'Dep', 'Exempt'})}

    def get_workflow_page(self, filters=None, page=1, page_size=25):
        from slicing_dashboard.reporting.workflow_history import history_page
        return history_page(self._workflow_history(), filters, page, page_size)

    def mark_workflow_notification_read(self, identifier):
        from slicing_dashboard.reporting.workflow_history import notice_link
        history = self._workflow_history()
        notice = history.mark_read(identifier)
        return notice_link(notice), history.notifications()

    def mark_all_workflow_notifications_read(self):
        return self._workflow_history().mark_all_read()

    def _get_reporting_name(self, uid, username):
        """Apply account exclusions consistently, including case-variant observations."""
        account = str(username or '').casefold()
        for record in getattr(self, 'user_mapping_full', {}).values():
            if str(record.get('id') or '').casefold() == account:
                if record.get('mapping_type') == 'Exempt':
                    return 'Exempt'
                if record.get('mapping_type') == 'New':
                    return 'Unassigned'
        return self._get_canonical_name(uid, username)

    @_per_refresh
    def get_todays_work_df(self, target_date: (str | None)=None,
        force_refresh: bool=False) -> pd.DataFrame:
        """Preserve each observed task/day, counting its latest submission once that day."""
        from slicing_dashboard.management.periods import today_iso
        from slicing_dashboard.processing.daily_work import aggregate_daily_work
        from slicing_dashboard.reporting.dashboard_reports import day_for_ui, merge_daily_reports

        current_date = today_iso()
        target_date = target_date or current_date
        datetime.strptime(target_date, '%Y-%m-%d')
        key = f'verified_daily_work_{target_date}'
        cached = getattr(self, '_cache', {}).get(key)
        records = self._load_daily_report_records(target_date, target_date)
        stored = records.get(target_date)
        previous_date = (datetime.strptime(current_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        kind = (stored or {}).get('metadata', {}).get('capture_kind')
        reconcile = target_date == previous_date and kind != 'verified_audit_import' and (
            force_refresh or kind != 'day_end_reconciliation')
        if stored and not reconcile and (target_date < current_date or not force_refresh):
            view = day_for_ui(stored, target_date, self._get_reporting_name)
            view['metadata']['closed'] = target_date < current_date
            result = pd.DataFrame(view['rows'])
            result.attrs.update(view['metadata'])
            return result
        if target_date < previous_date or target_date > current_date:
            result = pd.DataFrame()
            result.attrs.update(available=False, coverage='unavailable', target_date=target_date,
                                timezone='Asia/Kolkata', is_snapshot=False)
            return result
        try:
            source = self._daily_work_reader().fetch(target_date)
            _, evidence = aggregate_daily_work(
                source['tasks'], source['returned_accounts'], source['reviews'],
                target_date, self._get_reporting_name, requests=source.get('requests', []),
            )
            metadata = {'source': 'task submissions and batch-return history',
                        'captured_at': source['captured_at'], 'target_date': target_date,
                        'timezone': 'Asia/Kolkata', 'is_snapshot': False, 'observed_on': current_date,
                        'capture_kind': 'daily_observation' if target_date == current_date else 'day_end_reconciliation'}
            with self._daily_report_lock:
                record = merge_daily_reports(self._daily_report_records.get(target_date),
                                             {'date': target_date, 'tasks': evidence, 'metadata': metadata})
                if not self._persist_daily_report(record):
                    record['metadata']['persistence_error'] = 'Daily evidence could not be saved; this observation is held in memory only.'
                self._daily_report_records[target_date] = record
            view = day_for_ui(record, target_date, self._get_reporting_name)
            result = pd.DataFrame(view['rows'])
            result.attrs.update(view['metadata'])
            if not hasattr(self, '_cache'):
                self._cache = {}
            self._cache[key] = {'rows': view['rows'], 'metadata': view['metadata']}
            self.server_is_live = True
            self.is_using_snapshot = False
            self.last_sync_error = None
            self.last_sync_time = source['captured_at']
            return result
        except Exception as error:
            self.last_sync_error = str(error)
            self.is_using_snapshot = True
            if stored:
                cached = day_for_ui(stored, target_date, self._get_reporting_name)
            if cached:
                result = pd.DataFrame(cached['rows'])
                result.attrs.update(cached.get('metadata', {}), available=True, coverage='observed',
                                    is_snapshot=True, error=str(error))
                return result
            result = pd.DataFrame()
            result.attrs.update(error=str(error), target_date=target_date, is_snapshot=False,
                                available=False, coverage='unavailable', timezone='Asia/Kolkata')
            return result

    @_per_refresh
    def get_live_rework_by_user(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Fetch actual tasks with status 'slice_rework' from API grouped by canonical user."""
        cache_key = 'live_slice_rework'
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]

        if not self.scraper.is_authenticated:
            self.scraper.login()
        if not self.scraper._users:
            self.scraper._fetch_users()

        rework_stats: dict[str, dict[str, Any]] = {}
        try:
            page = 1
            has_more = True
            while has_more:
                url = f'{self.scraper._base_url}/api/slice/tasks'
                params = {'status': 'slice_rework', 'page_size': 200, 'page': page}
                res = self.scraper._client.get(url, params=params)
                res.raise_for_status()
                data = res.json()
                items = data.get('data', [])
                if not items:
                    break
                for it in items:
                    uid = it.get('slicer_id')
                    uname = self.scraper._get_username(uid)
                    canonical = self._get_canonical_name(uid, uname)
                    if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                        continue
                    dur = float(it.get('duration_seconds', 0) or 0)
                    if canonical not in rework_stats:
                        rework_stats[canonical] = {'count': 0, 'duration': 0.0}
                    rework_stats[canonical]['count'] += 1
                    rework_stats[canonical]['duration'] += dur
                meta = data.get('meta', {})
                has_more = meta.get('has_more', False)
                page += 1
            self._cache[cache_key] = rework_stats
            return rework_stats
        except Exception as e:
            print(f'Error fetching live rework tasks: {e}')
            if self._data is not None and not self._data.empty and 'status' in self._data.columns:
                rework_df = self._data[self._data['status'] == 'slice_rework']
                for _, r in rework_df.iterrows():
                    canonical = self._get_canonical_name(r.get('user_id'), r.get('user_name'))
                    if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                        continue
                    dur = float(r.get('duration_seconds', 0) or 0)
                    if canonical not in rework_stats:
                        rework_stats[canonical] = {'count': 0, 'duration': 0.0}
                    rework_stats[canonical]['count'] += 1
                    rework_stats[canonical]['duration'] += dur
                return rework_stats
            return {}

    def get_settlement_df(self, breakdown_df: pd.DataFrame) -> pd.DataFrame:
        """Calculate multi-column settlement overview (Jul 1-Aug 7, Aug 8-Aug 31, and Remaining Unsettled)."""
        if not hasattr(self, 'settlement_history') or not self.settlement_history:
            from slicing_dashboard.config import PROJECT_ROOT
            settlement_path = PROJECT_ROOT / 'config' / 'settlement_history.json'
            if settlement_path.exists():
                with open(settlement_path) as f:
                    self.settlement_history = json.load(f)
            else:
                self.settlement_history = {}

        users_settlement = self.settlement_history.get('users', {})

        # Map current completed duration from breakdown_df
        current_work_map = {}
        if not breakdown_df.empty:
            for _, row in breakdown_df.iterrows():
                current_work_map[row['User']] = float(row.get('Completed Duration', 0) or 0)

        # Include all canonical users from settlement history or current breakdown
        all_canonical_users = sorted(set(users_settlement.keys()) | set(current_work_map.keys()))
        all_canonical_users = [u for u in all_canonical_users if u not in ['Admin', 'Test', 'Dep', 'user-None', '', 'TOTAL', '(unassigned)']]

        records = []
        for user in all_canonical_users:
            u_info = users_settlement.get(user, {})
            b1_hours = float(u_info.get('batch_jul1_aug7', {}).get('total_hours', 0.0) or 0.0)
            b2_hours = float(u_info.get('batch_aug8_aug31', {}).get('total_hours', 0.0) or 0.0)
            b3_hours = float(u_info.get('batch_sep1_sep30', {}).get('total_hours', 0.0) or 0.0)
            total_settled_hours = float(u_info.get('total_settled', {}).get('total_hours', 0.0) or 0.0)

            b1_sec = b1_hours * 3600.0
            b2_sec = b2_hours * 3600.0
            b3_sec = b3_hours * 3600.0
            total_settled_sec = total_settled_hours * 3600.0

            curr_work_sec = current_work_map.get(user, 0.0)
            remaining_sec = curr_work_sec

            records.append({
                'User': user,
                'Jul 1 - Aug 7 (Paid)': b1_sec,
                'Aug 8 - Aug 31 (Paid)': b2_sec,
                'Sep 1 - Sep 30 (Paid)': b3_sec,
                'Total Settled (Sep 30)': total_settled_sec,
                'Current Work (Unsettled)': curr_work_sec,
                'Remaining Payable': remaining_sec,
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        # Add TOTAL summary row
        total_row = {
            'User': 'TOTAL',
            'Jul 1 - Aug 7 (Paid)': df['Jul 1 - Aug 7 (Paid)'].sum(),
            'Aug 8 - Aug 31 (Paid)': df['Aug 8 - Aug 31 (Paid)'].sum(),
            'Sep 1 - Sep 30 (Paid)': df['Sep 1 - Sep 30 (Paid)'].sum(),
            'Total Settled (Sep 30)': df['Total Settled (Sep 30)'].sum(),
            'Current Work (Unsettled)': df['Current Work (Unsettled)'].sum(),
            'Remaining Payable': df['Remaining Payable'].sum(),
        }
        df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
        return df

    def get_available_periods(self) -> list[dict]:
        """Return date ranges without mutating persisted settlement records."""
        from slicing_dashboard.management.periods import available_periods
        return available_periods(getattr(self, 'settlement_periods', []))

    @_per_refresh
    def fetch_all_assigned_tasks_live(self, force_refresh: bool = False) -> dict:
        """Read the current assigned inventory, never a historical batch snapshot."""
        return self._current_task_stats('slice_assigned', force_refresh)

    @_per_refresh
    @_serialized_source
    def _current_task_stats(self, status, force_refresh=False):
        from slicing_dashboard.processing.current_queue import fetch_queue
        self.refresh_shared_configuration()
        # Only an in-process, short-lived capture can be reused. Persisted
        # snapshot entries have no capture clock and cannot prove current state.
        cached = getattr(self, '_current_queue_cache', {}).get(status)
        if cached and not force_refresh and perf_counter() - cached[0] < SOURCE_TTL_SECONDS:
            tasks = cached[1]
        else:
            if cached:
                self._current_queue_cache.pop(status, None)
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError('Current assignment login failed')
            if not self.scraper._users:
                self.scraper._fetch_users()
            tasks = fetch_queue(self.scraper, status)
            if not hasattr(self, '_current_queue_cache'):
                self._current_queue_cache = {}
            self._current_queue_cache[status] = (perf_counter(), tasks)
        canonical_assigned = {}
        for t in tasks:
            username = t.get('slicer') or self.scraper._get_username(t.get('slicer_id'))
            if not username:
                continue
            canonical = self._get_reporting_name(t.get('slicer_id'), username)
            if str(canonical).casefold() in {'admin', 'test', 'dep', 'exempt', 'user-none', '', '(unassigned)', 'unassigned'}:
                continue
            if canonical not in canonical_assigned:
                canonical_assigned[canonical] = {
                    'backlog_dur': 0.0,
                    'backlog_cnt': 0,
                    'raw_ids': set(),
                    'accounts': {},
                    'segments': {},
                }
            stats = canonical_assigned[canonical]
            duration = float(t.get('duration_seconds', 0) or 0)
            stats['backlog_dur'] += duration
            stats['backlog_cnt'] += 1
            stats['raw_ids'].add(username)
            account = stats['accounts'].setdefault(username, {'duration': 0.0, 'count': 0})
            account['duration'] += duration
            account['count'] += 1
            key = (username, t.get('slice_batch'), t.get('assignment_batch_id') or t.get('batch_id'),
                   t.get('assigned_at') or t.get('assignment_date'))
            segment = stats['segments'].setdefault(key, {**t, 'duration': 0.0, 'count': 0})
            segment['duration'] += duration
            segment['count'] += 1
        for stats in canonical_assigned.values():
            stats['segments'] = list(stats['segments'].values())
        return canonical_assigned

    def get_detailed_pending_assigned_df(
        self,
        start_date: (str | None) = None,
        end_date: (str | None) = None,
        force_refresh: bool = False,
        overview_data: dict | None = None,
        include_pending: bool = True,
    ) -> pd.DataFrame:
        """Calculate user-level pending (split by Leader, Auditor, Admin) and assigned (New vs Rework) plus Assignable Pool."""
        if not start_date or not end_date:
            today_str = datetime.now().strftime('%Y-%m-%d')
            start_date = start_date or today_str
            end_date = end_date or today_str

        records = []
        pool = self.get_assignable_pool(force_refresh)
        if pool['available']:
            records.append({'User': 'Assignable Pool', 'Stage': 'Assignable (Pool)',
                            'Duration': pool['duration_seconds'], 'Count': pool['task_count'], 'RawID': ''})

        current_pending = self.get_pending_review_df(force_refresh) if include_pending else pd.DataFrame()

        # Current task status is authoritative. Batch assignment status and
        # total batch duration can remain unchanged after tasks are submitted.
        assigned_records = []
        queue_errors = []
        from slicing_dashboard.processing.current_queue import assignment_metadata
        from slicing_dashboard.management.periods import today_iso
        today = datetime.strptime(today_iso(), '%Y-%m-%d').date()
        returns = getattr(self, '_batch_returns_cache', {})
        returns = list(returns.values()) if isinstance(returns, dict) else returns
        batches = list(self._batches_master_cache.values())
        for status, stage in (('slice_assigned', 'New Assigned'), ('slice_rework', 'Rework Assigned')):
            try:
                users = (self.fetch_all_assigned_tasks_live(force_refresh=force_refresh)
                         if status == 'slice_assigned'
                         else self._current_task_stats(status, force_refresh))
                for canonical, stats in users.items():
                    for amounts in stats.get('segments', []):
                        metadata = assignment_metadata(amounts, batches, returns)
                        assigned = metadata['AssignedDate']
                        days = max(0, (today - datetime.strptime(assigned, '%Y-%m-%d').date()).days) if assigned else 0
                        account = amounts.get('slicer') or self.scraper._get_username(amounts.get('slicer_id'))
                        assigned_records.append({
                            'User': canonical, 'ID': account, 'Stage': stage,
                            'Duration': amounts['duration'], 'Count': amounts['count'],
                            **metadata, 'DaysAssigned': days, 'IDs': account,
                        })
            except Exception as error:
                # Neither dated overview totals nor old ledger rows establish
                # current assignments. Surface failure instead of stale hours.
                queue_errors.append(stage + ': ' + type(error).__name__)

        # Process pending reviews
        pending_df = pd.DataFrame(records)
        if not pending_df.empty:
            def _aggregate_raw_ids(ids_series):
                all_ids = set()
                for val in ids_series:
                    if not val:
                        continue
                    if isinstance(val, str) and ',' in val:
                        all_ids.update([v.strip() for v in val.split(',') if v.strip()])
                    else:
                        all_ids.add(val)
                return sorted(list(all_ids))

            pending_df = pending_df.groupby(['User', 'Stage'], as_index=False).agg({
                'Duration': 'sum',
                'Count': 'sum',
                'RawID': _aggregate_raw_ids
            })

            def format_ids(ids_list):
                if not ids_list: return ''
                chunks = [ids_list[i:i+3] for i in range(0, len(ids_list), 3)]
                return '<br>'.join(', '.join(chunk) for chunk in chunks)

            pending_df['IDs'] = pending_df['RawID'].apply(format_ids)
            pending_df = pending_df.drop(columns=['RawID'])
        else:
            pending_df = pd.DataFrame(columns=['User', 'Stage', 'Duration', 'Count', 'IDs'])

        # Process assigned / rework segments by ID
        if assigned_records:
            assigned_df = pd.DataFrame(assigned_records)
            assigned_df = assigned_df.groupby(
                ['User', 'ID', 'Stage', 'AssignedDate', 'DaysAssigned', 'AssignmentDateBasis'],
                as_index=False
            ).agg({
                'Duration': 'sum',
                'Count': 'sum',
                'BatchID': lambda x: ', '.join([str(b) for b in x if b]),
            })
            assigned_df['IDs'] = assigned_df['ID']
        else:
            assigned_df = pd.DataFrame(columns=['User', 'ID', 'Stage', 'Duration', 'Count', 'AssignedDate', 'DaysAssigned', 'BatchID', 'IDs'])

        res_df = pd.concat([pending_df, current_pending, assigned_df], ignore_index=True)
        res_df.attrs['assignment_errors'] = queue_errors
        res_df.attrs['assignable_error'] = pool['error']
        res_df.attrs['assignable_captured_at'] = pool['captured_at']
        res_df.attrs.update(current_pending.attrs)
        return res_df

    def get_pending_review_df(self, force_refresh=False):
        self.refresh_shared_configuration(force=force_refresh)
        from slicing_dashboard.processing.pending_review import pending_frame
        return pending_frame(self, force_refresh)

    def invalidate_pending_review(self):
        """Call after a successful review mutation or changed workflow evidence."""
        from slicing_dashboard.processing.pending_review import capture_for
        capture_for(self).invalidate()

    def run_sync_pipeline(self) -> bool:
        """Run the full extract → process → push-to-DB pipeline in-process.

        This replaces the subprocess call to `cli run --daily` so it works
        on Vercel serverless (no `uv`, no writable filesystem for CSVs).

        Returns True on success, False on failure.
        """
        try:
            from slicing_dashboard.extraction.dashboard import DashboardExtractor
            from slicing_dashboard.processing.cleaning import clean_dataframe
            from slicing_dashboard.processing.deduplication import deduplicate
            from slicing_dashboard.processing.normalization import normalize_dataframe
            from slicing_dashboard.processing.validation import validate_extraction
            from slicing_dashboard.processing.transitions import build_transitions
            from slicing_dashboard.db import DatabaseManager

            settings = self.settings

            # Step 1: Extract
            extractor = DashboardExtractor(settings)
            scraper = extractor._get_scraper()
            if not scraper.login():
                print("Sync pipeline: login failed")
                return False
            result = scraper.extract()
            if not result.success or not result.records:
                print(f"Sync pipeline: extraction failed — {result.errors}")
                return False

            # Save raw snapshot to disk if possible (local), ignore errors (Vercel)
            try:
                extractor._save_raw_snapshot(result)
            except Exception:
                pass

            # Step 2: Process
            raw_data = [record.raw_data for record in result.records]
            df = pd.DataFrame(raw_data)

            ext_validation = validate_extraction(df)
            if not ext_validation.is_valid:
                print(f"Sync pipeline: validation failed — {ext_validation.errors}")
                return False

            df, _ = clean_dataframe(df)

            # Load requests for normalization (best-effort from raw dir)
            requests_list = []
            try:
                from slicing_dashboard.config import RAW_DIR
                for requests_file in sorted(RAW_DIR.rglob('requests_*.json')):
                    import json as _json
                    with open(requests_file) as rf:
                        req_data = _json.load(rf)
                    requests_list.extend(req_data.get('records', []))
                if requests_list:
                    requests_dict = {r.get('id'): r for r in requests_list if r.get('id') is not None}
                    requests_list = list(requests_dict.values())
            except Exception:
                pass

            df, _ = normalize_dataframe(df, timezone=settings.timezone, requests=requests_list)
            df, _, _ = deduplicate(df)

            # Build transitions from reviewed tasks (best-effort)
            reviewed_tasks_list = []
            try:
                from slicing_dashboard.config import RAW_DIR as _RAW_DIR
                for reviewed_file in sorted(_RAW_DIR.rglob('reviewed_tasks_*.json')):
                    import json as _json
                    with open(reviewed_file) as rvf:
                        rev_data = _json.load(rvf)
                    reviewed_tasks_list.extend(rev_data.get('records', []))
                if reviewed_tasks_list:
                    import json as _json
                    unique_revs = {_json.dumps(r, sort_keys=True): r for r in reviewed_tasks_list}
                    reviewed_tasks_list = list(unique_revs.values())
            except Exception:
                pass

            transitions_df = build_transitions(df, reviewed_tasks_list)

            # Step 3: Save to local CSVs if filesystem writable
            try:
                from slicing_dashboard.config import PROJECT_ROOT
                slicing_path = PROJECT_ROOT / 'data' / 'processed' / 'slicing_master.csv'
                transitions_path = PROJECT_ROOT / 'data' / 'processed' / 'transitions_master.csv'
                slicing_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(slicing_path, index=False)
                transitions_df.to_csv(transitions_path, index=False)
                print(f"Sync pipeline: saved {len(df)} records to {slicing_path}")
            except Exception as e:
                print(f"Sync pipeline: local CSV write skipped: {e}")

            # Step 4: Push to database (if available)
            try:
                db_manager = DatabaseManager()
                if db_manager.is_connected():
                    db_manager.upsert_slicing_master(df)
                    db_manager.upsert_slicing_history_snapshot(df)
                    db_manager.upsert_transitions_master(transitions_df)
                    print("Sync pipeline: database updated successfully")
                else:
                    print("Sync pipeline: no database connection, skipping DB push")
            except Exception as e:
                print(f"Sync pipeline: DB push skipped ({e})")

            # Step 5: Update in-memory caches with fresh data
            self._cache.clear()
            self._data = df
            self._transitions_data = transitions_df
            self._last_loaded = datetime.now()

            scraper.close()
            return True

        except Exception as e:
            print(f"Sync pipeline error: {e}")
            import traceback
            traceback.print_exc()
            return False

    @_per_refresh
    def _fetch_efficiency_summary(self, start_date: str, end_date: str, role: int = 2) -> dict:
        response = self.scraper._client.get(
            f"{self.scraper._base_url}/api/dashboard/annotator-efficiency-overall",
            params={"start_date": start_date, "end_date": end_date, "role": role},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json().get("summary", {})

    @_per_refresh
    def _fetch_efficiency_items(self, start_date: str, end_date: str, role: int = 2) -> list:
        items, page, expected, identities = [], 1, None, set()
        while True:
            response = self.scraper._client.get(
                f"{self.scraper._base_url}/api/dashboard/annotator-efficiency",
                params={"start_date": start_date, "end_date": end_date, "role": role,
                        "page": page, "page_size": 200},
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                raise ValueError('Invalid efficiency response')
            page_items = payload['items']
            if payload.get('total') is not None:
                from slicing_dashboard.processing.pending_review import number
                total = number(payload['total'], count=True)
                if expected is not None and total != expected:
                    raise ValueError('Efficiency inventory changed during pagination')
                expected = total
            for item in page_items:
                if not isinstance(item, dict) or not item.get('username'):
                    raise ValueError('Missing efficiency account')
                identity = str(item['user_id']) if item.get('user_id') is not None else item['username'].casefold()
                if identity in identities:
                    raise ValueError('Repeated efficiency account')
                identities.add(identity)
            items.extend(page_items)
            if expected is not None and len(items) == expected:
                return items
            if expected is not None and len(items) > expected:
                raise ValueError('Inconsistent efficiency total')
            if not page_items or (expected is None and len(page_items) < 200):
                if expected is not None and len(items) != expected:
                    raise ValueError('Incomplete efficiency response')
                return items
            if page >= 100:
                raise ValueError('Efficiency pagination limit reached')
            page += 1

    @_per_refresh
    @_serialized_source
    def fetch_annotator_efficiency(
        self,
        start_date: str,
        end_date: str,
        role: int = 2,
        force_refresh: bool = False,
        include_summary: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch efficiency metrics directly from the API with safe fallback.

        Returns:
            (summary_dict, items_list)
        """
        cache_key = f"eff_{role}_{start_date}_{end_date}"
        summary_key = f'{cache_key}_has_summary'
        clock = getattr(self, '_source_cache_times', {}).get(cache_key, float('-inf'))
        if not force_refresh and perf_counter() - clock < SOURCE_TTL_SECONDS and cache_key in self._cache and (not include_summary or self._cache.get(summary_key, True)):
            return self._cache[cache_key]

        try:
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError("Login failed")

            # Most charts need only per-user rows. The much larger all-time
            # pending range does not need an extra overall aggregation query.
            summary = self._fetch_efficiency_summary(start_date, end_date, role) if include_summary else {}
            has_summary = include_summary
            if not include_summary:
                scope = _REFRESH_SCOPE.get()
                summary_read = ('_fetch_efficiency_summary', (('start_date', start_date), ('end_date', end_date), ('role', role)))
                prior_summary = scope['reads'].get(summary_read) if scope is not None and scope['manager'] is self else None
                if prior_summary is not None:
                    summary = prior_summary.result()
                    has_summary = True
            items = self._fetch_efficiency_items(start_date, end_date, role)

            res = (summary, items)
            self._cache[cache_key] = res
            self._cache[summary_key] = has_summary
            if not hasattr(self, '_source_cache_times'):
                self._source_cache_times = {}
            self._source_cache_times[cache_key] = perf_counter()
            self.server_is_live = True
            self.is_using_snapshot = False
            self.last_sync_error = None
            self._save_snapshot()
            return res
        except Exception as e:
            self.server_is_live = False
            self.is_using_snapshot = True
            self.last_sync_error = str(e)
            getattr(self, '_source_cache_times', {}).pop(cache_key, None)
            # Callers can show exact-range saved data explicitly, but never
            # substitute a different period or silently call a failed read empty.
            saved_summary, saved_items = self._cache.get(cache_key, ({}, []))
            return ({**saved_summary, '_source_error': type(e).__name__}, saved_items)

    def get_slice_data_overview_df(
        self,
        start_date: str,
        end_date: str,
        use_raw_names: bool = True,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Calculate detailed metrics matching the original Slicing Dashboard.

        Columns: Username, Total Count, Total Duration, Completed Slice Count/Duration,
        Submitted Slice Count/Duration, Pending Leader Review Count/Duration,
        Pending Auditor Review Count/Duration, Pending Admin Review Count/Duration,
        Error Video Pending Review Count/Duration, Rework Slice Count/Duration.
        """
        def format_duration_str(seconds):
            if pd.isna(seconds) or seconds is None:
                return '00:00:00'
            sec = int(round(float(seconds)))
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            return f'{h:02d}:{m:02d}:{s:02d}'

        def format_count_dur(count, duration):
            c = int(count or 0)
            d_str = format_duration_str(duration)
            return f"{c} / {d_str}"

        # 1. Try fetching from live API first
        try:
            summary, items = self.fetch_annotator_efficiency(
                start_date=start_date,
                end_date=end_date,
                role=2,
                force_refresh=force_refresh,
            )

            rows = []
            # Summary row: All Slicers
            rows.append({
                'Username': 'All Slicers',
                'Total Count': int(summary.get('total_count', 0) or 0),
                'Total Duration': format_duration_str(summary.get('total_duration_seconds', 0)),
                'Completed Slice Count/Duration': format_count_dur(
                    summary.get('completed_count', 0), summary.get('completed_duration_seconds', 0)
                ),
                'Submitted Slice Count/Duration': format_count_dur(
                    summary.get('submitted_count', 0), summary.get('submitted_duration_seconds', 0)
                ),
                'Pending Leader Review Count/Duration': format_count_dur(
                    summary.get('leader_review_count', 0), summary.get('leader_review_duration_seconds', 0)
                ),
                'Pending Auditor Review Count/Duration': format_count_dur(
                    summary.get('auditor_review_count', 0), summary.get('auditor_review_duration_seconds', 0)
                ),
                'Pending Admin Review Count/Duration': format_count_dur(
                    summary.get('admin_review_count', 0), summary.get('admin_review_duration_seconds', 0)
                ),
                'Error Video Pending Review Count/Duration': format_count_dur(
                    summary.get('error_review_count', 0), summary.get('error_review_duration_seconds', 0)
                ),
                'Rework Slice Count/Duration': format_count_dur(
                    summary.get('rework_count', 0), summary.get('rework_duration_seconds', 0)
                ),
            })

            # User rows
            for it in items:
                raw_user = it.get('username') or f"user-{it.get('user_id')}"
                disp_user = raw_user if use_raw_names else self._get_canonical_name(it.get('user_id'), raw_user)
                if not use_raw_names and disp_user in ['Admin', 'Test', 'Dep', 'user-None', '']:
                    continue

                # Exclude accounts with zero activity / unsubmitted work
                tot_c = int(it.get('total_count', 0) or 0)
                tot_d = float(it.get('total_duration_seconds', 0) or 0)
                sub_c = int(it.get('submitted_count', 0) or 0)
                comp_c = int(it.get('completed_count', 0) or 0)
                rew_c = int(it.get('rework_count', 0) or 0)
                lead_c = int(it.get('leader_review_count', 0) or 0)
                aud_c = int(it.get('auditor_review_count', 0) or 0)
                adm_c = int(it.get('admin_review_count', 0) or 0)
                if tot_c <= 0 and tot_d <= 0 and sub_c <= 0 and comp_c <= 0 and rew_c <= 0 and lead_c <= 0 and aud_c <= 0 and adm_c <= 0:
                    continue

                rows.append({
                    'Username': disp_user,
                    'Total Count': int(it.get('total_count', 0) or 0),
                    'Total Duration': format_duration_str(it.get('total_duration_seconds', 0)),
                    'Completed Slice Count/Duration': format_count_dur(
                        it.get('completed_count', 0), it.get('completed_duration_seconds', 0)
                    ),
                    'Submitted Slice Count/Duration': format_count_dur(
                        it.get('submitted_count', 0), it.get('submitted_duration_seconds', 0)
                    ),
                    'Pending Leader Review Count/Duration': format_count_dur(
                        it.get('leader_review_count', 0), it.get('leader_review_duration_seconds', 0)
                    ),
                    'Pending Auditor Review Count/Duration': format_count_dur(
                        it.get('auditor_review_count', 0), it.get('auditor_review_duration_seconds', 0)
                    ),
                    'Pending Admin Review Count/Duration': format_count_dur(
                        it.get('admin_review_count', 0), it.get('admin_review_duration_seconds', 0)
                    ),
                    'Error Video Pending Review Count/Duration': format_count_dur(
                        it.get('error_review_count', 0), it.get('error_review_duration_seconds', 0)
                    ),
                    'Rework Slice Count/Duration': format_count_dur(
                        it.get('rework_count', 0), it.get('rework_duration_seconds', 0)
                    ),
                })

            res_df = pd.DataFrame(rows)
            return res_df
        except Exception as e:
            print(f"Warning: Failed to fetch live annotator efficiency: {e}. Falling back to master CSV.")

        # 2. Offline fallback to master CSV if API fails
        if self._data is None:
            self.load_data()
        df = self._data
        if df.empty:
            return pd.DataFrame()

        # Determine grouping column
        if use_raw_names:
            group_col = 'user_id'
        else:
            df = df.copy()
            df['canonical_user'] = df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row.get('user_name', '')), axis=1)
            group_col = 'canonical_user'

        if not use_raw_names:
            df = df[~df[group_col].isin(['Admin', 'Test', 'Dep', 'user-None', ''])]

        records = []
        for user, group in df.groupby(group_col):
            total_count = len(group)
            total_dur = group['duration_seconds'].sum()

            def get_stats(status_list):
                subset = group[
                    (group['status_normalized'].isin(status_list)) & 
                    (group['completed_date'] >= start_date) & 
                    (group['completed_date'] <= end_date)
                ]
                count = len(subset)
                dur = subset['duration_seconds'].sum()
                return count, dur

            comp_count, comp_dur = get_stats(['slice_completed'])
            pl_count, pl_dur = get_stats(['slice_submitted'])
            paud_count, paud_dur = get_stats(['slice_pending_auditor_review'])
            padm_count, padm_dur = get_stats(['slice_pending_admin_review'])
            err_count, err_dur = get_stats(['video_error_review', 'video_error_confirmed'])
            rew_count, rew_dur = get_stats(['slice_rework'])

            sub_count = pl_count + paud_count + padm_count
            sub_dur = pl_dur + paud_dur + padm_dur

            records.append({
                'Username': user,
                'Total Count': total_count,
                'Total Duration': format_duration_str(total_dur),
                'Completed Slice Count/Duration': f"{comp_count} / {format_duration_str(comp_dur)}",
                'Submitted Slice Count/Duration': f"{sub_count} / {format_duration_str(sub_dur)}",
                'Pending Leader Review Count/Duration': f"{pl_count} / {format_duration_str(pl_dur)}",
                'Pending Auditor Review Count/Duration': f"{paud_count} / {format_duration_str(paud_dur)}",
                'Pending Admin Review Count/Duration': f"{padm_count} / {format_duration_str(padm_dur)}",
                'Error Video Pending Review Count/Duration': f"{err_count} / {format_duration_str(err_dur)}",
                'Rework Slice Count/Duration': f"{rew_count} / {format_duration_str(rew_dur)}"
            })

        res_df = pd.DataFrame(records)
        if not res_df.empty:
            res_df = res_df.sort_values('Username')
        return res_df

    def generate_individual_report(
        self,
        individual: str,
        target_date: str | None = None,
        target_month: str | None = None,
        output_dir: (str | Path | None) = None,
        force_refresh: bool = False,
    ) -> Path:
        """Generate an individual performance report workbook for a user."""
        if force_refresh or self._data is None or self._transitions_data is None:
            self.load_data()

        from slicing_dashboard.reporting.individual import generate_individual_report_data
        from slicing_dashboard.reporting.excel import write_individual_report

        report_data = generate_individual_report_data(
            master_df=self._data,
            transitions_df=self._transitions_data,
            individual=individual,
            target_date=target_date,
            target_month=target_month,
            user_mapping=self.user_mapping,
        )
        return write_individual_report(report_data, output_dir=output_dir)
