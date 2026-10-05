"""
Data manager for the Dash dashboard.
Fetches data from the dashboard API and structures it into Pandas DataFrames.
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from slicing_dashboard.config import get_settings
from slicing_dashboard.scraper.http_scraper import HTTPScraper


from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class DataManager:

    def __init__(self):
        self.settings = get_settings()
        self.scraper = HTTPScraper(self.settings)
        self._cache = {}
        self._daily_cache = {}
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
            from slicing_dashboard.db import DatabaseManager
            db = DatabaseManager()
            db_snap = db.load_dashboard_snapshot()
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
            self.last_sync_time = snap.get("last_sync_time", self.last_sync_time)

    def _save_snapshot(self) -> None:
        """Persist current cache to disk and database as snapshot."""
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
            }
            self._snapshot_payload = payload

            # 1. Save to disk (DATA_DIR locally, /tmp on Vercel)
            for target in [self._snapshot_path, Path("/tmp/snapshot_cache.json")]:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                    break
                except Exception:
                    continue

            # 2. Save to database if connected (for Vercel persistence across all lambdas)
            try:
                from slicing_dashboard.db import DatabaseManager
                db = DatabaseManager()
                db.save_dashboard_snapshot(payload)
            except Exception:
                pass
        except Exception as e:
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



    def fetch_dashboard_data(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> dict:
        """Fetches overview dashboard data with safe snapshot fallback."""
        cache_key = f'{start_date}_{end_date}'
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]

        # If server is known down, don't stall — return snapshot cache immediately
        if not force_refresh and not self.server_is_live and cache_key in self._cache:
            self.is_using_snapshot = True
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
            self._cache[cache_key] = data
            self.server_is_live = True
            self.is_using_snapshot = False
            self.last_sync_error = None
            self._save_snapshot()
            return data
        except Exception as e:
            self.server_is_live = False
            self.is_using_snapshot = True
            self.last_sync_error = str(e)
            if cache_key in self._cache:
                return self._cache[cache_key]
            # Fallback to default overview in snapshot
            def_ov = self._snapshot_payload.get('cache', {}).get('default_overview')
            if def_ov:
                return def_ov
            for k, v in self._cache.items():
                if isinstance(v, dict) and "metrics" in v:
                    return v
            return {"metrics": {}, "breakdowns": {"slice_user_breakdown": [], "slice_funnel": []}}

    def get_summary_kpis(self, start_date: str, end_date: str,
        selected_users: (list[str] | None) = None,
        force_refresh: bool = False) -> dict:
        """Fetch summary KPIs matching the official Slice Data Overview platform."""
        try:
            curr_summary, curr_items = self.fetch_annotator_efficiency(
                start_date=start_date, end_date=end_date, role=2, force_refresh=force_refresh
            )
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
                    'assignable_duration': metrics.get('overview_slice_assignable_remaining_duration_seconds', 0),
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
                'assignable_duration': metrics.get('overview_slice_assignable_remaining_duration_seconds', 0),
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
                'assignable_duration': metrics.get('overview_slice_assignable_remaining_duration_seconds', 0),
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
                force_refresh=force_refresh,
            )
            canonical_stats: dict[str, dict[str, Any]] = {}
            for it in items:
                raw_u = it.get('username', '')
                canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
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
                canonical_stats[canonical]['Completed Duration'] += float(it.get('completed_duration_seconds', 0) or 0.0)
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
            if records:
                return pd.DataFrame(records)
        except Exception as e:
            print(f"Warning: Failed to fetch user breakdown from efficiency API: {e}. Falling back.")

        error_durations = {}
        error_counts = {}
        if self._data is not None and not self._data.empty:
            try:
                df = self._data
                mask = (df['is_completed'] == True) & (df['completed_date'] >= start_date) & (df['completed_date'] <= end_date) & (df['completion_type'] == 'error')
                err_df = df[mask].copy()
                if not err_df.empty:
                    err_df['canonical_user'] = err_df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row.get('user_name', '')), axis=1)
                    for user, group in err_df.groupby('canonical_user'):
                        error_durations[user] = group['duration_seconds'].sum()
                        error_counts[user] = len(group)
            except Exception:
                pass

        data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
        breakdowns = data.get('breakdowns', {}).get('slice_user_breakdown', [])

        user_stats = {}
        for entry in breakdowns:
            uid = entry.get('user_id')
            username = self.scraper._get_username(uid)
            canonical = self._get_canonical_name(uid, username)
            if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                continue

            if canonical not in user_stats:
                user_stats[canonical] = {
                    'Completed Tasks': 0,
                    'normal_duration': 0,
                    'total_duration_api': 0,
                    'api_error_count': 0,
                }

            user_stats[canonical]['Completed Tasks'] += entry.get('completed_count', 0) or 0
            user_stats[canonical]['normal_duration'] += entry.get('normal_completed_duration_seconds', 0) or 0
            user_stats[canonical]['total_duration_api'] += entry.get('duration_seconds', 0) or 0
            user_stats[canonical]['api_error_count'] += entry.get('error_confirmed_count', 0) or 0

        records = []
        for user, stats in user_stats.items():
            err_dur = error_durations.get(user, 0)
            err_count = error_counts.get(user) if user in error_counts else stats['api_error_count']
            completed_dur = stats['normal_duration'] + err_dur

            records.append({
                'User': user,
                'Completed Tasks': stats['Completed Tasks'],
                'Completed Duration': completed_dur,
                'Submitted Tasks': stats['Completed Tasks'],
                'Submitted Duration': completed_dur,
                'Error Count': err_count,
                'Total Duration': stats['total_duration_api'],
                'Rework Duration': 0.0,
                'Rework Count': 0,
                'New Work Duration': stats['normal_duration'],
            })

        if not records and self._snapshot_payload.get('user_breakdown_records'):
            return pd.DataFrame(self._snapshot_payload['user_breakdown_records'])

        return pd.DataFrame(records)

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
                        force_refresh=force_refresh,
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
                results = dict(executor.map(fetch_single_day, dates_to_fetch))

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

    def sync_batch_returns(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Sync live batch return history from upstream API and merge into persistent dataset.
        
        Uses an append-only merge strategy so historical batch return data is permanently
        preserved even if upstream endpoints purge older history.
        """
        now = datetime.now()
        if not force_refresh and self._batch_returns_cache:
            if not self.server_is_live:
                return self._batch_returns_cache
            if self._last_batch_returns_sync and (now - self._last_batch_returns_sync).total_seconds() < 120:
                return self._batch_returns_cache

        try:
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    return self._batch_returns_cache

            if not self.scraper._users:
                self.scraper._fetch_users()

            url = f"{self.scraper._base_url}/api/requests/batch-return-history"
            existing_map = {r.get("event_id"): r for r in self._batch_returns_cache if r.get("event_id")}

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
                        items = r.json().get("data", [])
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
                    return []
                return []

            users_list = list(self.scraper._users.items())
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
        }
        for target in [self._batches_master_path, Path("/tmp/batches_master.json")]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                break
            except Exception:
                continue

    def sync_batches_master(
        self, start_date: str, end_date: str, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """Sync ground-truth batches from upstream API incrementally into persistent master ledger.
        
        Uses an append-only merge strategy with exact durations and return counts so historical
        batches are permanently preserved even if upstream endpoints purge older history.
        """
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        if not force_refresh and self._batches_master_cache:
            if not self.server_is_live:
                return self._batches_master_cache
            if self._last_batches_sync and (now - self._last_batches_sync).total_seconds() < 60:
                return self._batches_master_cache

        try:
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    return self._batches_master_cache

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

            cached_dates = set()
            for b in self._batches_master_cache.values():
                d = b.get("batch_date")
                if d:
                    cached_dates.add(d)

            dates_to_fetch = []
            cur = s_dt
            while cur <= e_dt:
                d_str = cur.strftime(fmt)
                # Always refresh recent active days (today, yesterday) or any date not yet cached
                if force_refresh or d_str in [today_str, yesterday_str] or d_str not in cached_dates:
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
                return day_str, []

            with ThreadPoolExecutor(max_workers=min(10, max(1, len(dates_to_fetch)))) as ex:
                results = list(ex.map(fetch_day_batches, dates_to_fetch))

            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            updated = False
            for day_str, b_list in results:
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

            if updated or force_refresh:
                self._save_batches_master(self._batches_master_cache)
                self._last_batches_sync = now

            return self._batches_master_cache
        except Exception as e:
            print(f"Warning: Failed to sync batches master: {e}")
            return self._batches_master_cache

    def get_batch_rework_ratio_df(self, start_date: str, end_date: str, force_refresh: bool = False) -> pd.DataFrame:
        """Calculate verified batch rework distribution across 0, 1, 2, 3, 4, 5+ reworks.
        
        Combines exact batch entities and counts from persistent batches master ledger
        with true completed/rework hours from the annotator efficiency API.
        """
        try:
            self.sync_batches_master(start_date, end_date, force_refresh=force_refresh)
            summary, items = self.fetch_annotator_efficiency(start_date, end_date, role=2, force_refresh=force_refresh)

            batches = list(self._batches_master_cache.values())
            
            returns = self.sync_batch_returns(force_refresh=force_refresh)
            range_returns = [
                r for r in returns
                if start_date <= str(r.get("returned_at", ""))[:10] <= end_date
            ]
            returns_in_range_by_batch = defaultdict(int)
            for r in range_returns:
                bid = r.get("batch_id")
                if bid:
                    returns_in_range_by_batch[bid] += 1

            returned_batch_ids = set(returns_in_range_by_batch.keys())

            def is_batch_submitted(b: dict) -> bool:
                """Return True only if batch has been submitted or has work activity / return history."""
                status = b.get("status", "")
                if status == "batch_member_assigned":
                    if (
                        (b.get("completed_count", 0) or 0) > 0
                        or (b.get("pending_review_count", 0) or 0) > 0
                        or (b.get("return_count", 0) or 0) > 0
                        or (b.get("rework_count", 0) or 0) > 0
                        or b.get("batch_id") in returned_batch_ids
                    ):
                        return True
                    return False
                return True

            filtered = [
                b for b in batches
                if (start_date <= b.get("batch_date", "") <= end_date or b.get("batch_id") in returned_batch_ids)
                and b.get("canonical_user") not in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"]
                and is_batch_submitted(b)
            ]

            user_results: dict[str, dict[str, Any]] = {}
            for b in filtered:
                raw_u = b.get("username", "")
                canon = b.get("canonical_user") or self._get_canonical_name(b.get("assignee_id", 0), raw_u)
                if not canon or canon in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"]:
                    continue
                if canon not in user_results:
                    user_results[canon] = {
                        "tiers": [0] * 6,
                        "total_batches": 0,
                        "comp_sec": 0.0,
                    }
                user_results[canon]["total_batches"] += 1
                user_results[canon]["comp_sec"] += float(b.get("total_duration_seconds", 0) or 0.0)

            # --- TRUE RETURN COUNT CALCULATION FROM LIVE BATCHES & RETURNS ---
            used_csv = False
            if filtered:
                for b in filtered:
                    canon = b.get("canonical_user") or self._get_canonical_name(b.get("assignee_id", 0), b.get("username", ""))
                    if canon in user_results:
                        bid = b.get("batch_id")
                        if bid in returns_in_range_by_batch:
                            rc = returns_in_range_by_batch[bid]
                        elif returns:
                            rc = 0
                        else:
                            rc = b.get("return_count", 0) or 0
                        if rc == 0 and (b.get("rework_count", 0) or 0) > 0:
                            rc = 1
                        tier = min(5, max(0, int(rc)))
                        user_results[canon]["tiers"][tier] += 1
            else:
                # Fallback to historical CSVs if no batches are found in master cache
                try:
                    from slicing_dashboard.config import PROJECT_ROOT
                    sm_path = PROJECT_ROOT / 'data' / 'processed' / 'slicing_master.csv'
                    tm_path = PROJECT_ROOT / 'data' / 'processed' / 'transitions_master.csv'
                    if sm_path.exists() and tm_path.exists():
                        sm = pd.read_csv(sm_path, usecols=['id', 'slice_batch', 'status_normalized', 'user_id', 'completed_date'])
                        sm = sm[(sm['completed_date'].astype(str) >= start_date) & (sm['completed_date'].astype(str) <= end_date)]
                        if not sm.empty:
                            tm = pd.read_csv(tm_path, usecols=['task_id', 'type', 'date'])
                            task_to_batch = sm.set_index('id')['slice_batch'].to_dict()
                            task_to_user = sm.set_index('id')['user_id'].to_dict()
                            batch_in_rework = {}
                            batch_to_user = {}
                            for _, row in sm.iterrows():
                                sb = row['slice_batch']
                                if not pd.isna(sb):
                                    uid = row['user_id']
                                    canon = self._get_canonical_name(0, uid) if pd.notna(uid) else None
                                    if canon:
                                        batch_to_user[sb] = canon
                                    if row['status_normalized'] == 'slice_rework':
                                        batch_in_rework[sb] = True
                            
                            batch_return_dates = defaultdict(set)
                            for _, row in tm.iterrows():
                                if row['type'] in ['leader_returned', 'auditor_returned', 'admin_returned']:
                                    sb = task_to_batch.get(row['task_id'])
                                    if sb is not None and not pd.isna(sb):
                                        batch_return_dates[sb].add(str(row['date'])[:10])
                                        
                            true_user_tiers = defaultdict(lambda: [0]*6)
                            for sb in sm['slice_batch'].dropna().unique():
                                canon = batch_to_user.get(sb)
                                if not canon or canon not in user_results: continue
                                
                                ret_count = len(batch_return_dates.get(sb, set()))
                                if ret_count == 0 and batch_in_rework.get(sb):
                                    ret_count = 1
                                    
                                if ret_count > 0:
                                    tier = min(5, max(0, ret_count))
                                    true_user_tiers[canon][tier] += 1
                                    
                            for canon, d in user_results.items():
                                tiers = true_user_tiers.get(canon, [0]*6)
                                total_returned_batches = sum(tiers[1:])
                                tot = d["total_batches"]
                                zero_reworks = max(0, tot - total_returned_batches)
                                tiers[0] = zero_reworks
                                user_results[canon]["tiers"] = tiers
                            used_csv = True
                except Exception as e:
                    print(f"Failed to calculate true returns from CSV: {e}")
            # --------------------------------------------------------

            # Incorporate verified completed/submitted duration and rework counts from efficiency API
            user_eff_dur: dict[str, float] = defaultdict(float)
            user_eff_rew: dict[str, int] = defaultdict(int)
            for it in items:
                raw_u = it.get("username", "")
                canon = self._get_canonical_name(it.get("user_id"), raw_u)
                if not canon or canon in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"]:
                    continue
                comp_d = float(it.get("completed_duration_seconds", 0) or 0.0)
                sub_d = float(it.get("submitted_duration_seconds", 0) or 0.0)
                work_d = float(it.get("work_duration_seconds", 0) or 0.0)
                rew_c = int(it.get("rework_count", 0) or 0)
                user_eff_dur[canon] += max(comp_d, sub_d, work_d)
                user_eff_rew[canon] += rew_c

            # Also incorporate live rework
            live_rework = self.get_live_rework_by_user()
            for canon, lr in live_rework.items():
                if canon not in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"]:
                    user_eff_rew[canon] += lr.get("count", 0)
                    user_eff_dur[canon] = max(user_eff_dur[canon], lr.get("duration", 0.0))

            for canon, dur in user_eff_dur.items():
                if dur > 0 or user_eff_rew[canon] > 0:
                    if canon in user_results:
                        if dur > 0:
                            user_results[canon]["comp_sec"] = max(user_results[canon]["comp_sec"], dur)
                        # If user has known reworks but all batches are in tier 0 (No Rework), reflect reworks
                        if user_eff_rew[canon] > 0 and sum(user_results[canon]["tiers"][1:]) == 0:
                            user_results[canon]["tiers"][1] = 1
                            user_results[canon]["tiers"][0] = max(0, user_results[canon]["total_batches"] - 1)
                    else:
                        # User worked but wasn't in filtered batches
                        b_count = max(1, round(dur / 3600.0)) if dur > 0 else 1
                        t = [0] * 6
                        if user_eff_rew[canon] > 0:
                            t[1] = 1
                            t[0] = max(0, b_count - 1)
                        else:
                            t[0] = b_count
                        user_results[canon] = {
                            "tiers": t,
                            "total_batches": b_count,
                            "comp_sec": dur,
                        }

            records = []
            for u, d in sorted(user_results.items()):
                tot_b = d["total_batches"]
                if tot_b <= 0:
                    continue
                dur_hrs = round(d["comp_sec"] / 3600.0, 2)
                avg_hrs = round(dur_hrs / max(1, tot_b), 2)
                t = d["tiers"]
                records.append({
                    "User": u,
                    "No Rework": t[0],
                    "1 Rework": t[1],
                    "2 Reworks": t[2],
                    "3 Reworks": t[3],
                    "4 Reworks": t[4],
                    "5+ Reworks": t[5],
                    "Total Batches": tot_b,
                    "Total Duration (hrs)": dur_hrs,
                    "Avg Batch Duration (hrs)": avg_hrs,
                })

            return pd.DataFrame(records)
        except Exception as e:
            print(f"Error in get_batch_rework_ratio_df: {e}")
            return pd.DataFrame()

    def get_todays_work_df(self, target_date: (str | None)=None,
        force_refresh: bool=False) -> pd.DataFrame:
        """Fetch daily work broken down by user in real time directly from efficiency API."""
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')

        try:
            summary, items = self.fetch_annotator_efficiency(
                start_date=target_date,
                end_date=target_date,
                role=2,
                force_refresh=force_refresh,
            )
            live_rework = self.get_live_rework_by_user()

            user_stats: dict[str, dict[str, Any]] = {}
            for it in items:
                raw_u = it.get('username', '')
                canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)', 'Exempt']:
                    continue
                if canonical not in user_stats:
                    user_stats[canonical] = {
                        'sub_cnt': 0, 'sub_dur': 0.0,
                        'comp_cnt': 0, 'comp_dur': 0.0,
                        'rew_cnt': 0, 'rew_dur': 0.0,
                        'work_dur': 0.0,
                        'raw_ids': set()
                    }
                user_stats[canonical]['sub_cnt'] += int(it.get('submitted_count', 0) or 0)
                user_stats[canonical]['sub_dur'] += float(it.get('submitted_duration_seconds', 0.0) or 0.0)
                user_stats[canonical]['comp_cnt'] += int(it.get('completed_count', 0) or 0)
                user_stats[canonical]['comp_dur'] += float(it.get('completed_duration_seconds', 0.0) or 0.0)
                user_stats[canonical]['rew_cnt'] += int(it.get('rework_count', 0) or 0)
                user_stats[canonical]['rew_dur'] += float(it.get('rework_duration_seconds', 0.0) or 0.0)
                user_stats[canonical]['work_dur'] += float(it.get('work_duration_seconds', 0.0) or 0.0)
                if raw_u:
                    user_stats[canonical]['raw_ids'].add(raw_u)

            # Blend live in-progress rework tasks (status == 'slice_rework')
            for canon, lr in live_rework.items():
                if canon in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)', 'Exempt']:
                    continue
                if canon not in user_stats:
                    user_stats[canon] = {
                        'sub_cnt': 0, 'sub_dur': 0.0,
                        'comp_cnt': 0, 'comp_dur': 0.0,
                        'rew_cnt': lr.get('count', 0),
                        'rew_dur': lr.get('duration', 0.0),
                        'work_dur': 0.0,
                        'raw_ids': set(),
                        'live_rew_cnt': lr.get('count', 0),
                        'live_rew_dur': lr.get('duration', 0.0)
                    }
                else:
                    user_stats[canon]['live_rew_cnt'] = lr.get('count', 0)
                    user_stats[canon]['live_rew_dur'] = lr.get('duration', 0.0)

            records = []
            for user, st in sorted(user_stats.items()):
                sub_cnt = st['sub_cnt']
                comp_cnt = st['comp_cnt']
                sub_dur = st['sub_dur']
                comp_dur = st['comp_dur']
                work_dur = st['work_dur']
                rew_dur = st['rew_dur']
                rew_cnt = st['rew_cnt']
                live_cnt = st.get('live_rew_cnt', 0)
                live_dur = st.get('live_rew_dur', 0.0)

                # Rework is the maximum of server efficiency rework duration and live rework duration
                eff_rew_dur = max(rew_dur, live_dur)
                eff_rew_cnt = max(rew_cnt, live_cnt)

                # Total duration is max of submitted, completed, or actual work duration
                # If rework duration is larger (in-progress rework), ensure total duration covers it
                total_dur = max(sub_dur, comp_dur, work_dur, eff_rew_dur)
                new_dur = max(0.0, total_dur - eff_rew_dur)
                total_cnt = max(sub_cnt, comp_cnt, eff_rew_cnt)

                if total_dur <= 0 and total_cnt <= 0:
                    continue

                h = int(total_dur // 3600)
                m = int((total_dur % 3600) // 60)
                s = int(total_dur % 60)
                wh_str = f"{h:02d}:{m:02d}:{s:02d}"

                rework_pct_val = (eff_rew_dur / total_dur * 100.0) if total_dur > 0 else 0.0

                raw_ids_list = sorted(list(st['raw_ids']))
                raw_id_str = raw_ids_list[0] if len(raw_ids_list) == 1 else (",".join(raw_ids_list) if raw_ids_list else "")

                records.append({
                    'User': user,
                    'Total Tasks': total_cnt,
                    'Total Duration': total_dur,
                    'New Videos (First Time)': new_dur,
                    'Reworks': eff_rew_dur,
                    'Working Hours Seconds': total_dur,
                    'Working Hours': wh_str,
                    'Rework %': f"{rework_pct_val:.1f}%",
                    'RawID': raw_id_str,
                })

            if records:
                return pd.DataFrame(records)
            return pd.DataFrame()
        except Exception as e:
            print(f'Warning: Failed to fetch daily work from efficiency API: {e}. Falling back to CSV.')

        # 2. Offline fallback to master data transitions if available
        if self._data is not None and self._transitions_data is not None:
            try:
                df = self._data
                trans_df = self._transitions_data
                post_submit_statuses = [
                    'slice_submitted', 'slice_pending_auditor_review',
                    'slice_pending_admin_review', 'slice_completed',
                    'video_error_confirmed'
                ]
                submitted_today = df[
                    (df['completed_date'] == target_date) &
                    (df['status'].isin(post_submit_statuses))
                ].copy()
                submitted_today = submitted_today[~submitted_today['user_name'].isin(['Admin', 'Test', 'Dep', 'user-None', '', 'Exempt'])]
                if not submitted_today.empty:
                    task_ids = submitted_today['id'].tolist()
                    rework_by_ids = set(submitted_today[submitted_today['rework_by'].fillna('') != '']['id'])
                    returned_ids = set(trans_df[(trans_df['task_id'].isin(task_ids)) & (trans_df['type'].isin(['leader_returned', 'auditor_returned']))]['task_id'])
                    submit_counts = trans_df[trans_df['type'] == 'submitted'].groupby('task_id').size()
                    multi_submit_ids = set(submit_counts[submit_counts > 1].index) & set(task_ids)
                    all_rework_ids = rework_by_ids | returned_ids | multi_submit_ids

                    submitted_today['is_rework'] = submitted_today['id'].isin(all_rework_ids)
                    submitted_today['canonical_user'] = submitted_today['user_name'].apply(
                        lambda u: self._get_canonical_name('', u))

                    records = []
                    for user, group in submitted_today.groupby('canonical_user'):
                        rework_df = group[group['is_rework']]
                        first_time_df = group[~group['is_rework']]
                        records.append({
                            'User': user,
                            'Total Tasks': len(group),
                            'Total Duration': group['duration_seconds'].sum(),
                            'New Videos (First Time)': first_time_df['duration_seconds'].sum(),
                            'Reworks': rework_df['duration_seconds'].sum()
                        })
                    if records:
                        return pd.DataFrame(records)
            except Exception:
                pass

        return pd.DataFrame()

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

    def fetch_all_assigned_tasks_live(self) -> dict:
        """Fetch all assigned tasks directly to bypass the date-filtering flaw of the overview API."""
        if not self.scraper.is_authenticated:
            self.scraper.login()
        if not self.scraper._users:
            self.scraper._fetch_users()
            
        tasks = []
        page = 1
        has_more = True
        while has_more:
            res = self.scraper._client.get(
                f'{self.scraper._base_url}/api/slice/tasks', 
                params={'status': 'slice_assigned', 'page_size': 200, 'page': page}
            )
            if res.status_code == 200:
                data = res.json().get('data', [])
                tasks.extend(data)
                meta = res.json().get('meta', {})
                has_more = meta.get('has_more', False)
                page += 1
            else:
                break
                
        # Aggregate by canonical user
        canonical_assigned = {}
        for t in tasks:
            username = t.get('slicer')
            if not username:
                continue
            
            canonical = self._get_canonical_name(0, username)
            if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                continue
                
            if canonical not in canonical_assigned:
                canonical_assigned[canonical] = {
                    'backlog_dur': 0.0,
                    'backlog_cnt': 0,
                    'raw_ids': set(),
                }
            
            canonical_assigned[canonical]['backlog_dur'] += float(t.get('duration_seconds', 0) or 0)
            canonical_assigned[canonical]['backlog_cnt'] += 1
            canonical_assigned[canonical]['raw_ids'].add(username)
            
        return canonical_assigned

    def get_detailed_pending_assigned_df(
        self,
        start_date: (str | None) = None,
        end_date: (str | None) = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Calculate user-level pending (split by Leader, Auditor, Admin) and assigned (New vs Rework) plus Assignable Pool."""
        if not start_date or not end_date:
            today_str = datetime.now().strftime('%Y-%m-%d')
            start_date = start_date or today_str
            end_date = end_date or today_str

        data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
        breakdowns = data.get('breakdowns', {})
        metrics = data.get('metrics', {})

        records = []

        # 1. Real-time Assignable Videos (Unassigned Pool)
        funnel_list = breakdowns.get('slice_funnel', [])
        funnel_map = {f.get('key'): f for f in funnel_list}
        assignable_dur = (
            funnel_map.get('pending_assign', {}).get('duration_seconds')
            or metrics.get('overview_slice_assignable_remaining_duration_seconds', 0)
        )
        assignable_count = funnel_map.get('pending_assign', {}).get('count', 0)
        records.append({
            'User': 'Assignable Pool',
            'Stage': 'Assignable (Pool)',
            'Duration': float(assignable_dur or 0),
            'Count': int(assignable_count or 0),
            'RawID': '',
        })

        # 2. Exact Pending Reviews per user from annotator efficiency API
        # Using a very wide date range (2020 to today) ensures we get ALL currently pending tasks,
        # bypassing the ~7500 record pagination limit of the slice/tasks endpoint which was hiding
        # older pending tasks (e.g., hiding 8 hours of Priya's 12 hours).
        today_str = datetime.now().strftime('%Y-%m-%d')
        try:
            _, eff_items = self.fetch_annotator_efficiency(
                start_date='2020-01-01',
                end_date=today_str,
                role=2,
                force_refresh=force_refresh,
            )
            for it in eff_items:
                raw_u = it.get('username', '')
                canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                    continue
                
                dur_lead = float(it.get('leader_review_duration_seconds', 0) or 0)
                cnt_lead = int(it.get('leader_review_count', 0) or 0)
                dur_aud = float(it.get('auditor_review_duration_seconds', 0) or 0)
                cnt_aud = int(it.get('auditor_review_count', 0) or 0)
                dur_adm = float(it.get('admin_review_duration_seconds', 0) or 0)
                cnt_adm = int(it.get('admin_review_count', 0) or 0)
                
                if dur_lead > 0 or cnt_lead > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Leader',
                        'Duration': dur_lead,
                        'Count': cnt_lead,
                        'RawID': raw_u,
                    })
                if dur_aud > 0 or cnt_aud > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Auditor',
                        'Duration': dur_aud,
                        'Count': cnt_aud,
                        'RawID': raw_u,
                    })
                if dur_adm > 0 or cnt_adm > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Admin',
                        'Duration': dur_adm,
                        'Count': cnt_adm,
                        'RawID': raw_u,
                    })
        except Exception as e:
            print(f"Warning: Failed to fetch exact pending review from efficiency API: {e}")

        # 3. Exact Real-time Assigned per user segmented by ID
        assigned_records = []
        today = datetime.now().date()
        active_batches = [
            b for b in self._batches_master_cache.values()
            if isinstance(b, dict) and b.get("status") in ["batch_member_assigned", "batch_rework"]
        ] if self._batches_master_cache else []

        if active_batches:
            for b in active_batches:
                c_user = b.get("canonical_user") or self._get_canonical_name(b.get("assignee_id", 0), b.get("username", ""))
                if c_user in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                    continue
                raw_u = b.get("username") or c_user
                b_date_str = b.get("batch_date") or today.strftime("%Y-%m-%d")
                try:
                    b_date = datetime.strptime(b_date_str, "%Y-%m-%d").date()
                    days = max(0, (today - b_date).days)
                except Exception:
                    days = 0
                stage = "Rework Assigned" if b.get("status") == "batch_rework" else "New Assigned"
                dur = float(b.get("total_duration_seconds", 0) or 0)
                cnt = int(b.get("task_count", 0) or 0)
                assigned_records.append({
                    "User": c_user,
                    "ID": raw_u,
                    "Stage": stage,
                    "Duration": dur,
                    "Count": cnt,
                    "AssignedDate": b_date_str,
                    "DaysAssigned": days,
                    "BatchID": b.get("batch_id", ""),
                    "IDs": raw_u,
                })
        else:
            # Fallback to live API or user breakdown if batches master cache is empty
            live_rework = self.get_live_rework_by_user(force_refresh=force_refresh)
            canonical_assigned: dict[str, dict[str, Any]] = {}
            try:
                canonical_assigned = self.fetch_all_assigned_tasks_live()
            except Exception as e:
                print(f"Warning: Failed to fetch exact assigned tasks live: {e}")
                user_breakdown = breakdowns.get('slice_user_breakdown', [])
                for entry in user_breakdown:
                    uid = entry.get('user_id')
                    if uid is None:
                        continue
                    username = self.scraper._get_username(uid)
                    canonical = self._get_canonical_name(uid, username)
                    if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                        continue
                    backlog_dur = float(entry.get('backlog_duration_seconds', 0) or 0)
                    backlog_cnt = int(entry.get('backlog_count', 0) or 0)
                    review_pend_dur = float(entry.get('review_pending_duration_seconds', 0) or 0)
                    review_pend_cnt = int(entry.get('review_pending_count', 0) or 0)
                    pure_assigned_dur = max(backlog_dur - review_pend_dur, 0.0)
                    pure_assigned_cnt = max(backlog_cnt - review_pend_cnt, 0)
                    if canonical not in canonical_assigned:
                        canonical_assigned[canonical] = {'backlog_dur': 0.0, 'backlog_cnt': 0, 'raw_ids': set()}
                    canonical_assigned[canonical]['backlog_dur'] += pure_assigned_dur
                    canonical_assigned[canonical]['backlog_cnt'] += pure_assigned_cnt
                    if username:
                        canonical_assigned[canonical]['raw_ids'].add(username)

            all_users = sorted(set(canonical_assigned.keys()) | set(live_rework.keys()))
            for canonical in all_users:
                stats = canonical_assigned.get(canonical, {'backlog_dur': 0.0, 'backlog_cnt': 0, 'raw_ids': set()})
                raw_ids = list(stats['raw_ids'])
                assigned_raw_id = raw_ids[0] if len(raw_ids) == 1 else (",".join(raw_ids) if raw_ids else canonical)
                rework_info = live_rework.get(canonical, {'count': 0, 'duration': 0.0})
                rework_dur = float(rework_info.get('duration', 0.0))
                rework_cnt = int(rework_info.get('count', 0))
                new_dur = max(stats['backlog_dur'] - rework_dur, 0.0)
                new_cnt = max(stats['backlog_cnt'] - rework_cnt, 0)
                if new_dur > 0 or new_cnt > 0:
                    assigned_records.append({
                        'User': canonical,
                        'ID': assigned_raw_id,
                        'Stage': 'New Assigned',
                        'Duration': new_dur,
                        'Count': new_cnt,
                        'AssignedDate': today.strftime('%Y-%m-%d'),
                        'DaysAssigned': 0,
                        'BatchID': '',
                        'IDs': assigned_raw_id,
                    })
                if rework_dur > 0 or rework_cnt > 0:
                    assigned_records.append({
                        'User': canonical,
                        'ID': assigned_raw_id,
                        'Stage': 'Rework Assigned',
                        'Duration': rework_dur,
                        'Count': rework_cnt,
                        'AssignedDate': today.strftime('%Y-%m-%d'),
                        'DaysAssigned': 0,
                        'BatchID': '',
                        'IDs': assigned_raw_id,
                    })

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
                ['User', 'ID', 'Stage', 'AssignedDate', 'DaysAssigned'],
                as_index=False
            ).agg({
                'Duration': 'sum',
                'Count': 'sum',
                'BatchID': lambda x: ', '.join([str(b) for b in x if b]),
            })
            assigned_df['IDs'] = assigned_df['ID']
        else:
            assigned_df = pd.DataFrame(columns=['User', 'ID', 'Stage', 'Duration', 'Count', 'AssignedDate', 'DaysAssigned', 'BatchID', 'IDs'])

        res_df = pd.concat([pending_df, assigned_df], ignore_index=True)
        return res_df

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

    def fetch_annotator_efficiency(
        self,
        start_date: str,
        end_date: str,
        role: int = 2,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch efficiency metrics directly from the API with safe fallback.

        Returns:
            (summary_dict, items_list)
        """
        cache_key = f"eff_{role}_{start_date}_{end_date}"
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]

        if not force_refresh and not self.server_is_live and cache_key in self._cache:
            self.is_using_snapshot = True
            return self._cache[cache_key]

        try:
            if not self.scraper.is_authenticated:
                if not self.scraper.login():
                    raise ConnectionError("Login failed")

            url = self.scraper._base_url

            # 1. Fetch overall summary
            overall_resp = self.scraper._client.get(
                f"{url}/api/dashboard/annotator-efficiency-overall",
                params={"start_date": start_date, "end_date": end_date, "role": role},
                timeout=5.0,
            )
            overall_resp.raise_for_status()
            summary = overall_resp.json().get("summary", {})

            # 2. Fetch all paginated user items
            items: list[dict[str, Any]] = []
            page = 1
            while True:
                eff_resp = self.scraper._client.get(
                    f"{url}/api/dashboard/annotator-efficiency",
                    params={
                        "start_date": start_date,
                        "end_date": end_date,
                        "role": role,
                        "page": page,
                        "page_size": 200,
                    },
                    timeout=5.0,
                )
                eff_resp.raise_for_status()
                page_json = eff_resp.json()
                page_items = page_json.get("items", [])
                if not page_items:
                    break
                items.extend(page_items)
                total = page_json.get("total", len(items))
                if len(items) >= total or len(page_items) < 200 or page > 20:
                    break
                page += 1

            res = (summary, items)
            self._cache[cache_key] = res
            self.server_is_live = True
            self.is_using_snapshot = False
            self.last_sync_error = None
            self._save_snapshot()
            return res
        except Exception as e:
            self.server_is_live = False
            self.is_using_snapshot = True
            self.last_sync_error = str(e)
            if cache_key in self._cache:
                return self._cache[cache_key]
            for k, v in self._cache.items():
                if k.startswith("eff_") and isinstance(v, tuple):
                    return v
            return ({}, [])

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
