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


from concurrent.futures import ThreadPoolExecutor
from typing import Any


class DataManager:

    def __init__(self):
        self.settings = get_settings()
        self.scraper = HTTPScraper(self.settings)
        self._cache = {}
        self._daily_cache = {}
        mapping_path = Path(self.settings.user_mapping_path)
        if not mapping_path.is_absolute():
            from slicing_dashboard.config import PROJECT_ROOT
            mapping_path = PROJECT_ROOT / self.settings.user_mapping_path
        if mapping_path.exists():
            with open(mapping_path) as f:
                self.user_mapping = json.load(f)
        else:
            self.user_mapping = {}

        from slicing_dashboard.config import PROJECT_ROOT, DATA_DIR
        settlement_path = PROJECT_ROOT / 'config' / 'settlement_history.json'
        if settlement_path.exists():
            with open(settlement_path) as f:
                self.settlement_history = json.load(f)
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

        # Always load master data and snapshot cache on startup
        self.load_data()
        self._load_snapshot()

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
            self.last_sync_time = snap.get("last_sync_time", self.last_sync_time)

    def _save_snapshot(self) -> None:
        """Persist current cache to disk and database as snapshot."""
        try:
            self.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload = {
                "last_sync_time": self.last_sync_time,
                "cache": self._cache,
                "daily_cache": self._daily_cache,
                "summary_kpis": self._snapshot_payload.get("summary_kpis", {}),
                "user_breakdown_records": self._snapshot_payload.get("user_breakdown_records", []),
                "available_users": self._snapshot_payload.get("available_users", []),
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
        for k, v in self.user_mapping.items():
            if k.lower() == username.lower():
                return v
        return username

    def fetch_dashboard_data(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> dict:
        """Fetches overview dashboard data with safe snapshot fallback."""
        cache_key = f'{start_date}_{end_date}'
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]

        # If server is known down, don't stall — return snapshot cache immediately
        if not self.server_is_live and cache_key in self._cache:
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
                worked_dur = max(comp_dur, subm_dur)
                new_work_dur = max(0.0, worked_dur - rew_dur)
                records.append({
                    'User': user,
                    'Completed Tasks': st['Completed Tasks'],
                    'Completed Duration': comp_dur,
                    'Submitted Tasks': st['Submitted Tasks'],
                    'Submitted Duration': subm_dur,
                    'Error Count': st['Error Count'],
                    'Total Duration': st['Total Duration'],
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
                        force_refresh=(dt_str == today_str and force_refresh),
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

    def get_todays_work_df(self, target_date: (str | None)=None,
        force_refresh: bool=False) -> pd.DataFrame:
        """Fetch daily work broken down by user in real time directly from efficiency API."""
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')

        # 1. Real-time API calculation directly from annotator efficiency endpoint
        try:
            summary, items = self.fetch_annotator_efficiency(
                start_date=target_date,
                end_date=target_date,
                role=2,
                force_refresh=force_refresh,
            )
            canonical_stats: dict[str, dict[str, Any]] = {}
            for it in items:
                raw_u = it.get('username', '')
                canonical = self._get_canonical_name(it.get('user_id'), raw_u)
                if canonical in ['Admin', 'Test', 'Dep', 'user-None', '', '(unassigned)']:
                    continue
                sub_cnt = int(it.get('submitted_count', 0) or 0)
                sub_dur = float(it.get('submitted_duration_seconds', 0) or 0.0)
                comp_cnt = int(it.get('completed_count', 0) or 0)
                comp_dur = float(it.get('completed_duration_seconds', 0) or 0.0)
                rew_cnt = int(it.get('rework_count', 0) or 0)
                rew_dur = float(it.get('rework_duration_seconds', 0) or 0.0)

                day_cnt = max(sub_cnt, comp_cnt)
                day_dur = max(sub_dur, comp_dur)

                if day_cnt > 0 or day_dur > 0 or rew_dur > 0:
                    if canonical not in canonical_stats:
                        canonical_stats[canonical] = {'tasks': 0, 'total_dur': 0.0, 'rework_dur': 0.0}
                    canonical_stats[canonical]['tasks'] += day_cnt
                    canonical_stats[canonical]['total_dur'] += day_dur
                    canonical_stats[canonical]['rework_dur'] += rew_dur

            records = []
            for user, st in sorted(canonical_stats.items()):
                new_dur = max(st['total_dur'] - st['rework_dur'], 0.0)
                records.append({
                    'User': user,
                    'Total Tasks': st['tasks'],
                    'Total Duration': st['total_dur'],
                    'New Videos (First Time)': new_dur,
                    'Reworks': st['rework_dur'],
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
                submitted_today = submitted_today[~submitted_today['user_name'].isin(['Admin', 'Test', 'Dep', 'user-None', ''])]
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
            total_settled_hours = float(u_info.get('total_settled', {}).get('total_hours', 0.0) or 0.0)

            b1_sec = b1_hours * 3600.0
            b2_sec = b2_hours * 3600.0
            total_settled_sec = total_settled_hours * 3600.0

            curr_work_sec = current_work_map.get(user, 0.0)
            remaining_sec = curr_work_sec

            records.append({
                'User': user,
                'Jul 1 - Aug 7 (Paid)': b1_sec,
                'Aug 8 - Aug 31 (Paid)': b2_sec,
                'Total Settled (Aug 31)': total_settled_sec,
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
            'Total Settled (Aug 31)': df['Total Settled (Aug 31)'].sum(),
            'Current Work (Unsettled)': df['Current Work (Unsettled)'].sum(),
            'Remaining Payable': df['Remaining Payable'].sum(),
        }
        df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
        return df

    def get_settlement_periods(self) -> tuple[tuple[str, str], tuple[str, str]]:
        """Calculate date ranges for (current_settlement_period, previous_settlement_period).

        Returns:
            ((curr_start, curr_end), (prev_start, prev_end))
        """
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        settlement_date_str = self.settlement_history.get("settlement_date", "2026-08-31")
        try:
            settlement_dt = datetime.strptime(settlement_date_str, "%Y-%m-%d")
            curr_start = (settlement_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            curr_start = today.replace(day=1).strftime("%Y-%m-%d")
        curr_end = today_str

        # Previous settled batch from settlement history (Aug 8 to Aug 31)
        prev_start = "2026-08-08"
        prev_end = settlement_date_str

        return (curr_start, curr_end), (prev_start, prev_end)

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
        })

        # 2. Exact Pending Reviews per user from annotator efficiency API
        try:
            _, eff_items = self.fetch_annotator_efficiency(
                start_date=start_date,
                end_date=end_date,
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
                    })
                if dur_aud > 0 or cnt_aud > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Auditor',
                        'Duration': dur_aud,
                        'Count': cnt_aud,
                    })
                if dur_adm > 0 or cnt_adm > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Admin',
                        'Duration': dur_adm,
                        'Count': cnt_adm,
                    })
        except Exception as e:
            print(f"Warning: Failed to fetch exact pending review from efficiency API: {e}")

        # 3. Real-time Assigned per user
        user_breakdown = breakdowns.get('slice_user_breakdown', [])
        live_rework = self.get_live_rework_by_user(force_refresh=force_refresh)

        canonical_assigned: dict[str, dict[str, Any]] = {}
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

            if canonical not in canonical_assigned:
                canonical_assigned[canonical] = {
                    'backlog_dur': 0.0,
                    'backlog_cnt': 0,
                    'review_dur': 0.0,
                    'review_cnt': 0,
                }
            canonical_assigned[canonical]['backlog_dur'] += backlog_dur
            canonical_assigned[canonical]['backlog_cnt'] += backlog_cnt
            canonical_assigned[canonical]['review_dur'] += review_pend_dur
            canonical_assigned[canonical]['review_cnt'] += review_pend_cnt

        all_users = sorted(set(canonical_assigned.keys()) | set(live_rework.keys()))
        for canonical in all_users:
            stats = canonical_assigned.get(canonical, {
                'backlog_dur': 0.0,
                'backlog_cnt': 0,
                'review_dur': 0.0,
                'review_cnt': 0,
            })
            backlog_dur = stats['backlog_dur']
            backlog_cnt = stats['backlog_cnt']
            review_pend_dur = stats['review_dur']
            review_pend_cnt = stats['review_cnt']

            pure_assigned_dur = max(backlog_dur - review_pend_dur, 0.0)
            pure_assigned_cnt = max(backlog_cnt - review_pend_cnt, 0)

            rework_info = live_rework.get(canonical, {'count': 0, 'duration': 0.0})
            rework_dur = float(rework_info.get('duration', 0.0))
            rework_cnt = int(rework_info.get('count', 0))

            new_dur = max(pure_assigned_dur - rework_dur, 0.0)
            new_cnt = max(pure_assigned_cnt - rework_cnt, 0)

            if new_dur > 0 or new_cnt > 0:
                records.append({
                    'User': canonical,
                    'Stage': 'New Assigned',
                    'Duration': new_dur,
                    'Count': new_cnt,
                })
            if rework_dur > 0 or rework_cnt > 0:
                records.append({
                    'User': canonical,
                    'Stage': 'Rework Assigned',
                    'Duration': rework_dur,
                    'Count': rework_cnt,
                })

        res_df = pd.DataFrame(records)
        if not res_df.empty:
            res_df = res_df.groupby(['User', 'Stage'], as_index=False).sum()
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

        if not self.server_is_live and cache_key in self._cache:
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

