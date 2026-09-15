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
        self._data = None
        self._transitions_data = None
        self._last_loaded = None

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
        return self.user_mapping.get(username, username)

    def fetch_dashboard_data(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> dict:
        """Fetches the overview dashboard data and user breakdown."""
        cache_key = f'{start_date}_{end_date}'
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]
        if not self.scraper.is_authenticated:
            self.scraper.login()
        if not self.scraper._users:
            self.scraper._fetch_users()
        overview_url = f'{self.scraper._base_url}/api/dashboard/overview'
        params = {'mode': 'slice', 'start_date': start_date, 'end_date': end_date}
        response = self.scraper._client.get(overview_url, params=params)
        response.raise_for_status()
        data = response.json()
        self._cache[cache_key] = data
        return data

    def get_summary_kpis(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> dict:
        data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
        metrics = data.get('metrics', {})
        from datetime import datetime, timedelta
        fmt = '%Y-%m-%d'
        dt_start = datetime.strptime(start_date, fmt)
        dt_end = datetime.strptime(end_date, fmt)
        diff = dt_end - dt_start
        prev_end = dt_start - timedelta(days=1)
        prev_start = prev_end - diff
        prev_data = self.fetch_dashboard_data(prev_start.strftime(fmt),
            prev_end.strftime(fmt), force_refresh)
        prev_metrics = prev_data.get('metrics', {})

        def calc_pct(curr, prev):
            if not prev:
                return 0.0
            return (curr - prev) / prev * 100.0

        return {
            'total_approved_duration': metrics.get('slice_completed_duration_seconds', 0),
            'prev_approved_duration': prev_metrics.get('slice_completed_duration_seconds', 0),
            'approved_pct': calc_pct(metrics.get('slice_completed_duration_seconds', 0), prev_metrics.get('slice_completed_duration_seconds', 0)),
            'total_error_duration': metrics.get('error_video_output_duration_seconds', 0),
            'prev_error_duration': prev_metrics.get('error_video_output_duration_seconds', 0),
            'error_pct': calc_pct(metrics.get('error_video_output_duration_seconds', 0), prev_metrics.get('error_video_output_duration_seconds', 0)),
            'rework_duration': metrics.get('overview_slice_rework_submitted_duration_seconds', 0),
            'prev_rework_duration': prev_metrics.get('overview_slice_rework_submitted_duration_seconds', 0),
            'rework_pct': calc_pct(metrics.get('overview_slice_rework_submitted_duration_seconds', 0), prev_metrics.get('overview_slice_rework_submitted_duration_seconds', 0)),
            'total_tasks': metrics.get('total_tasks', 0),
            'completed_tasks': metrics.get('slice_completed_count', 0),
            'prev_completed_tasks': prev_metrics.get('slice_completed_count', 0),
            'completed_pct': calc_pct(metrics.get('slice_completed_count', 0), prev_metrics.get('slice_completed_count', 0)),
            'assignable_duration': metrics.get('overview_slice_assignable_remaining_duration_seconds', 0),
            'total_backlog_duration': metrics.get('slice_backlog_duration_seconds', 0),
            'total_pending_duration': metrics.get('review_pending_duration_seconds', 0),
        }

    def get_user_breakdown_df(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> pd.DataFrame:
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
                'Error Count': err_count,
                'Total Duration': stats['total_duration_api']
            })

        return pd.DataFrame(records)

    def get_cumulative_df(self, start_date: str, end_date: str,
        force_refresh: bool=False) -> pd.DataFrame:
        """Fetch cumulative performance day-by-day in real time directly from the API."""
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        all_dates = [(start + timedelta(days=x)).strftime('%Y-%m-%d') for x in range((end - start).days + 1)]
        today_str = datetime.now().strftime('%Y-%m-%d')

        # Determine dates that need fetching
        dates_to_fetch = []
        for d in all_dates:
            if d == today_str:
                if force_refresh or d not in self._daily_cache:
                    dates_to_fetch.append(d)
            elif d not in self._daily_cache or force_refresh:
                dates_to_fetch.append(d)

        if dates_to_fetch:
            def fetch_single_day(dt_str):
                try:
                    return dt_str, self.fetch_dashboard_data(dt_str, dt_str, force_refresh=(dt_str == today_str and force_refresh))
                except Exception:
                    return dt_str, {}

            workers = min(len(dates_to_fetch), 5)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = dict(executor.map(fetch_single_day, dates_to_fetch))

            for d, data in results.items():
                day_breakdown = data.get('breakdowns', {}).get('slice_user_breakdown', [])
                day_users = {}
                for entry in day_breakdown:
                    uid = entry.get('user_id')
                    username = self.scraper._get_username(uid)
                    canonical = self._get_canonical_name(uid, username)
                    if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                        continue
                    completed_dur = entry.get('normal_completed_duration_seconds', 0) or 0
                    day_users[canonical] = day_users.get(canonical, 0) + completed_dur
                self._daily_cache[d] = day_users

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
        """Fetch today's work broken down by user in real time."""
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')

        # 1. Try master data transitions if available
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

        # 2. Real-time API calculation directly from overview endpoint
        try:
            data = self.fetch_dashboard_data(target_date, target_date, force_refresh)
            breakdowns = data.get('breakdowns', {}).get('slice_user_breakdown', [])
            records = []
            for entry in breakdowns:
                uid = entry.get('user_id')
                if uid is None:
                    continue
                username = self.scraper._get_username(uid)
                canonical = self._get_canonical_name(uid, username)
                if canonical in ['Admin', 'Test', 'Dep', 'user-None', '']:
                    continue
                completed_count = entry.get('completed_count', 0) or 0
                completed_dur = entry.get('normal_completed_duration_seconds', 0) or 0
                # In overview breakdown, duration_seconds includes backlog and review_pending.
                # Only completed work belongs in Today's Activity summary.
                if completed_count > 0 or completed_dur > 0:
                    records.append({
                        'User': canonical,
                        'Total Tasks': completed_count,
                        'Total Duration': completed_dur,
                        'New Videos (First Time)': completed_dur,
                        'Reworks': 0.0
                    })
            if records:
                res_df = pd.DataFrame(records)
                res_df = res_df.groupby('User', as_index=False).sum()
                return res_df
            return pd.DataFrame()
        except Exception as e:
            print(f'Error computing todays work from API: {e}')
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
        """Calculate paid vs remaining duration based on August 7th batch."""
        paid_batch = {'Aditya': 5 * 3600 + 24 * 60 + 50, 'Komal': 3 * 3600 +
            17 * 60 + 8, 'Priya': 1 * 3600 + 51 * 60 + 2, 'Rajni': 0,
            'Ranjeeta': 0, 'Riya': 0, 'Sanddep': 0}
        if breakdown_df.empty:
            return pd.DataFrame()
        records = []
        for _, row in breakdown_df.iterrows():
            user = row['User']
            total_dur = row['Completed Duration']
            paid = paid_batch.get(user, 0)
            remaining = max(total_dur - paid, 0)
            records.append({'User': user, 'Paid Duration': paid,
                'Remaining Duration': remaining, 'Total Duration': total_dur})
        df = pd.DataFrame(records)
        return df

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

        # Calculate review stage ratios from live review pressure
        rev_pressure = breakdowns.get('review_pressure', {})
        leader_dur_total = float(rev_pressure.get('slice_submitted_duration_seconds', 0) or 0)
        auditor_dur_total = float(rev_pressure.get('slice_auditor_review_duration_seconds', 0) or 0)
        admin_dur_total = float(rev_pressure.get('slice_admin_review_duration_seconds', 0) or 0)
        total_review_dur = leader_dur_total + auditor_dur_total + admin_dur_total

        if total_review_dur > 0:
            ratio_leader = leader_dur_total / total_review_dur
            ratio_auditor = auditor_dur_total / total_review_dur
            ratio_admin = admin_dur_total / total_review_dur
        else:
            ratio_leader, ratio_auditor, ratio_admin = 0.12, 0.26, 0.62

        leader_cnt_total = int(rev_pressure.get('slice_submitted_count', 0) or 0)
        auditor_cnt_total = int(rev_pressure.get('slice_auditor_review_count', 0) or 0)
        admin_cnt_total = int(rev_pressure.get('slice_admin_review_count', 0) or 0)
        total_review_cnt = leader_cnt_total + auditor_cnt_total + admin_cnt_total

        if total_review_cnt > 0:
            ratio_cnt_leader = leader_cnt_total / total_review_cnt
            ratio_cnt_auditor = auditor_cnt_total / total_review_cnt
            ratio_cnt_admin = admin_cnt_total / total_review_cnt
        else:
            ratio_cnt_leader, ratio_cnt_auditor, ratio_cnt_admin = 0.10, 0.28, 0.62

        # 2. Real-time Assigned and Pending Reviews per user
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

            # Pure assigned working workload (backlog minus review pending)
            pure_assigned_dur = max(backlog_dur - review_pend_dur, 0.0)
            pure_assigned_cnt = max(backlog_cnt - review_pend_cnt, 0)

            # Get exact rework tasks for this user from live API
            rework_info = live_rework.get(canonical, {'count': 0, 'duration': 0.0})
            rework_dur = float(rework_info.get('duration', 0.0))
            rework_cnt = int(rework_info.get('count', 0))

            # New assigned is pure assigned workload minus rework
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

            # Pending reviews split across Leader, Auditor, and Admin review
            if review_pend_dur > 0 or review_pend_cnt > 0:
                dur_leader = review_pend_dur * ratio_leader
                dur_auditor = review_pend_dur * ratio_auditor
                dur_admin = review_pend_dur * ratio_admin

                cnt_leader = round(review_pend_cnt * ratio_cnt_leader)
                cnt_auditor = round(review_pend_cnt * ratio_cnt_auditor)
                cnt_admin = max(0, review_pend_cnt - cnt_leader - cnt_auditor)

                if dur_leader > 0 or cnt_leader > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Leader',
                        'Duration': dur_leader,
                        'Count': cnt_leader,
                    })
                if dur_auditor > 0 or cnt_auditor > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Auditor',
                        'Duration': dur_auditor,
                        'Count': cnt_auditor,
                    })
                if dur_admin > 0 or cnt_admin > 0:
                    records.append({
                        'User': canonical,
                        'Stage': 'Pending Admin',
                        'Duration': dur_admin,
                        'Count': cnt_admin,
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


    def get_slice_data_overview_df(self, start_date: str, end_date: str, use_raw_names: bool=False) ->pd.DataFrame:
        """Calculate detailed metrics matching the original Slicing Dashboard.
        
        Columns: Total Count, Total Duration, Completed Slice Count/Duration,
        Submitted Slice Count/Duration, Pending Leader Review Count/Duration,
        Pending Auditor Review Count/Duration, Pending Admin Review Count/Duration,
        Error Video Count/Duration, Rework Slice Count/Duration.
        """
        if self._data is None:
            self.load_data()
        df = self._data
        if df.empty:
            return pd.DataFrame()
            
        def format_duration_str(seconds):
            if pd.isna(seconds) or seconds is None:
                return '00:00:00'
            seconds = int(seconds)
            h = seconds // 3600
            m = seconds % 3600 // 60
            s = seconds % 60
            return f'{h:02d}:{m:02d}:{s:02d}'
            
        # Determine grouping column
        if use_raw_names:
            # user_id stores the original username string from cleaning.py
            group_col = 'user_id'
        else:
            # Compute canonical user
            df = df.copy()
            df['canonical_user'] = df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row.get('user_name', '')), axis=1)
            group_col = 'canonical_user'
            
        # Ignore structural non-user accounts if using canonical
        if not use_raw_names:
            df = df[~df[group_col].isin(['Admin', 'Test', 'Dep', 'user-None', ''])]
            
        records = []
        for user, group in df.groupby(group_col):
            total_count = len(group)
            total_dur = group['duration_seconds'].sum()
            
            # Helper to get count and duration for a specific status
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
                'Error Video Count/Duration': f"{err_count} / {format_duration_str(err_dur)}",
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

