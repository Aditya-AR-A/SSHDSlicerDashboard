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


class DataManager:

    def __init__(self):
        self.settings = get_settings()
        self.scraper = HTTPScraper(self.settings)
        self._cache = {}
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

    def load_data(self) ->None:
        """Load slicing and transitions master data from database or CSV."""
        from slicing_dashboard.db import DatabaseManager
        db_manager = DatabaseManager()
        if db_manager.is_connected():
            try:
                df = db_manager.load_slicing_master()
                trans_df = db_manager.load_transitions_master()
                self._data = df
                self._transitions_data = trans_df
                self._last_loaded = datetime.now()
                return
            except Exception as e:
                pass
            pass
        from slicing_dashboard.config import PROJECT_ROOT
        slicing_path = (PROJECT_ROOT / 'data' / 'processed' /
            'slicing_master.csv')
        transitions_path = (PROJECT_ROOT / 'data' / 'processed' /
            'transitions_master.csv')
        if not slicing_path.exists():
            raise FileNotFoundError(f'Slicing master not found: {slicing_path}'
                )
        self._data = pd.read_csv(slicing_path)
        if transitions_path.exists():
            self._transitions_data = pd.read_csv(transitions_path)
        else:
            self._transitions_data = None
        self._last_loaded = datetime.now()

    def _get_canonical_name(self, uid: int, username: str) ->str:
        return self.user_mapping.get(username, username)

    def fetch_dashboard_data(self, start_date: str, end_date: str,
        force_refresh: bool=False) ->dict:
        """Fetches the overview dashboard data and user breakdown."""
        cache_key = f'{start_date}_{end_date}'
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key]
        if not self.scraper.is_authenticated:
            self.scraper.login()
            self.scraper._fetch_users()
        overview_url = f'{self.scraper._base_url}/api/dashboard/overview'
        params = {'mode': 'slice', 'start_date': start_date, 'end_date':
            end_date}
        response = self.scraper._client.get(overview_url, params=params)
        response.raise_for_status()
        data = response.json()
        self._cache[cache_key] = data
        return data

    def get_summary_kpis(self, start_date: str, end_date: str,
        force_refresh: bool=False) ->dict:
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
        return {'total_approved_duration': metrics.get(
            'slice_completed_duration_seconds', 0),
            'prev_approved_duration': prev_metrics.get(
            'slice_completed_duration_seconds', 0), 'approved_pct':
            calc_pct(metrics.get('slice_completed_duration_seconds', 0),
            prev_metrics.get('slice_completed_duration_seconds', 0)),
            'total_error_duration': metrics.get(
            'error_video_output_duration_seconds', 0),
            'prev_error_duration': prev_metrics.get(
            'error_video_output_duration_seconds', 0), 'error_pct':
            calc_pct(metrics.get('error_video_output_duration_seconds', 0),
            prev_metrics.get('error_video_output_duration_seconds', 0)),
            'rework_duration': metrics.get(
            'overview_slice_rework_submitted_duration_seconds', 0),
            'prev_rework_duration': prev_metrics.get(
            'overview_slice_rework_submitted_duration_seconds', 0),
            'rework_pct': calc_pct(metrics.get(
            'overview_slice_rework_submitted_duration_seconds', 0),
            prev_metrics.get(
            'overview_slice_rework_submitted_duration_seconds', 0)),
            'total_tasks': metrics.get('total_tasks', 0), 'completed_tasks':
            metrics.get('slice_completed_count', 0), 'prev_completed_tasks':
            prev_metrics.get('slice_completed_count', 0), 'completed_pct':
            calc_pct(metrics.get('slice_completed_count', 0), prev_metrics.
            get('slice_completed_count', 0))}

    def get_user_breakdown_df(self, start_date: str, end_date: str,
        force_refresh: bool=False) ->pd.DataFrame:
        if force_refresh or self._data is None:
            self.load_data()
            
        error_durations = {}
        error_counts = {}
        if self._data is not None and not self._data.empty:
            df = self._data
            mask = (df['is_completed'] == True) & (df['completed_date'] >= start_date) & (df['completed_date'] <= end_date) & (df['completion_type'] == 'error')
            err_df = df[mask].copy()
            if not err_df.empty:
                err_df['canonical_user'] = err_df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row.get('user_name', '')), axis=1)
                for user, group in err_df.groupby('canonical_user'):
                    error_durations[user] = group['duration_seconds'].sum()
                    error_counts[user] = len(group)
                    
        data = self.fetch_dashboard_data(start_date, end_date, force_refresh)
        breakdowns = data.get('breakdowns', {}).get('slice_user_breakdown', [])
        
        user_stats = {}
        for entry in breakdowns:
            username = self.scraper._get_username(entry.get('user_id'))
            canonical = self._get_canonical_name(entry.get('user_id'), username)
            if canonical in ['Admin', 'Test', 'Dep', 'user-None']:
                continue
                
            if canonical not in user_stats:
                user_stats[canonical] = {
                    'Completed Tasks': 0,
                    'normal_duration': 0,
                    'total_duration_api': 0,
                }
                
            user_stats[canonical]['Completed Tasks'] += entry.get('completed_count', 0) or 0
            user_stats[canonical]['normal_duration'] += entry.get('normal_completed_duration_seconds', 0) or 0
            user_stats[canonical]['total_duration_api'] += entry.get('duration_seconds', 0) or 0
            
        records = []
        for user, stats in user_stats.items():
            err_dur = error_durations.get(user, 0)
            err_count = error_counts.get(user, 0)
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
        force_refresh: bool=False) ->pd.DataFrame:
        """Fetch cumulative performance day-by-day."""
        if force_refresh or self._data is None:
            self.load_data()
        df = self._data
        if df.empty:
            return pd.DataFrame()
            
        mask = (df['is_completed'] == True) & (df['completed_date'] >= start_date) & (df['completed_date'] <= end_date)
        comp_df = df[mask].copy()
        comp_df = comp_df[~comp_df['user_name'].isin(['Admin', 'Test', 'Dep', 'user-None', ''])]
        
        if comp_df.empty:
            return pd.DataFrame()
            
        comp_df['canonical_user'] = comp_df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row.get('user_name', '')), axis=1)
        
        daily_df = comp_df.groupby(['completed_date', 'canonical_user'], as_index=False)['duration_seconds'].sum()
        pivot = daily_df.pivot(index='completed_date', columns='canonical_user', values='duration_seconds').fillna(0)
        
        from datetime import datetime, timedelta
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        all_dates = [(start + timedelta(days=x)).strftime('%Y-%m-%d') for x in range((end - start).days + 1)]
        pivot = pivot.reindex(all_dates, fill_value=0)
        
        cumulative_pivot = pivot.cumsum()
        cumulative_pivot.index.name = 'Date'
        return cumulative_pivot.reset_index()

    def get_todays_work_df(self, target_date: (str | None)=None,
        force_refresh: bool=False) ->pd.DataFrame:
        """Fetch today's work broken down by user, separating first-time and reworks.
        
        A task counts as 'submitted today' if:
        - Its completed_date == target_date (completed_date is derived from the
          last status change timestamp, so it captures when the task moved)
        - Its current status is post-submission (slice_submitted, 
          slice_pending_auditor_review, slice_pending_admin_review,
          slice_completed, video_error_confirmed)
        
        A task is a rework if ANY of:
        - rework_by field is set
        - It has a leader_returned or auditor_returned transition
        - It has been submitted more than once
        """
        if not target_date:
            target_date = datetime.now().strftime('%Y-%m-%d')
        try:
            if (force_refresh or self._data is None or self.
                _transitions_data is None):
                self.load_data()
            df = self._data
            trans_df = self._transitions_data

            # Find tasks submitted on target date using master data
            post_submit_statuses = [
                'slice_submitted', 'slice_pending_auditor_review',
                'slice_pending_admin_review', 'slice_completed',
                'video_error_confirmed'
            ]
            submitted_today = df[
                (df['completed_date'] == target_date) &
                (df['status'].isin(post_submit_statuses))
            ].copy()
            
            submitted_today = submitted_today[~submitted_today['user_name']
                .isin(['Admin', 'Test', 'Dep', 'user-None', ''])]
            
            if submitted_today.empty:
                return pd.DataFrame()
            
            # Build rework detection signals
            task_ids = submitted_today['id'].tolist()
            
            # Signal 1: rework_by field is set
            rework_by_ids = set(
                submitted_today[submitted_today['rework_by'].fillna('') != '']['id']
            )
            
            # Signal 2: task was ever returned by leader/auditor
            returned_ids = set(
                trans_df[
                    (trans_df['task_id'].isin(task_ids)) &
                    (trans_df['type'].isin(['leader_returned', 'auditor_returned']))
                ]['task_id']
            )
            
            # Signal 3: task was submitted more than once
            submit_counts = trans_df[
                trans_df['type'] == 'submitted'
            ].groupby('task_id').size()
            multi_submit_ids = set(
                submit_counts[submit_counts > 1].index
            ) & set(task_ids)
            
            # Union of all rework signals
            all_rework_ids = rework_by_ids | returned_ids | multi_submit_ids
            
            submitted_today['is_rework'] = submitted_today['id'].isin(all_rework_ids)
            submitted_today['canonical_user'] = submitted_today['user_name'].apply(
                lambda u: self._get_canonical_name('', u))
                
            records = []
            for user, group in submitted_today.groupby('canonical_user'):
                rework_df = group[group['is_rework']]
                first_time_df = group[~group['is_rework']]
                records.append({'User': user, 'Total Tasks': len(group),
                    'Total Duration': group['duration_seconds'].sum(),
                    'New Videos (First Time)': first_time_df[
                    'duration_seconds'].sum(), 'Reworks': rework_df[
                    'duration_seconds'].sum()})
            res_df = pd.DataFrame(records)
            return res_df
        except Exception as e:
            print(f'Error computing todays work: {e}')
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    def get_settlement_df(self, breakdown_df: pd.DataFrame) ->pd.DataFrame:
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

    def get_detailed_pending_assigned_df(self, force_refresh: bool=False) ->pd.DataFrame:
        """Calculate user-level pending (leader, auditor, admin) and assigned (new, rework) using local slicing_master."""
        if force_refresh or self._data is None:
            self.load_data()
        df = self._data
        if df.empty:
            return pd.DataFrame()
        df = df[~df['user_name'].isin(['Admin', 'Test', 'Dep', 'user-None',
            ''])]
        status_map = {'slice_submitted': 'Pending Leader',
            'slice_pending_auditor_review': 'Pending Auditor',
            'slice_pending_admin_review': 'Pending Admin', 'slice_assigned':
            'New Assigned', 'slicing': 'New Assigned', 'slice_rework':
            'Rework Assigned'}
        df = df[df['status'].isin(status_map.keys())].copy()
        df['Stage'] = df['status'].map(status_map)
        
        # Apply canonical name mapping
        df['canonical_user'] = df.apply(lambda row: self._get_canonical_name(row.get('user_id', ''), row['user_name']), axis=1)
        
        grouped = df.groupby(['canonical_user', 'Stage'], as_index=False)[
            'duration_seconds'].sum()
        grouped.rename(columns={'canonical_user': 'User', 'duration_seconds':
            'Duration'}, inplace=True)
        return grouped

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

            # Step 3: Push to database
            db_manager = DatabaseManager()
            if db_manager.is_connected():
                db_manager.upsert_slicing_master(df)
                db_manager.upsert_slicing_history_snapshot(df)
                db_manager.upsert_transitions_master(transitions_df)
                print("Sync pipeline: database updated successfully")
            else:
                print("Sync pipeline: no database connection, skipping DB push")

            # Step 4: Clear in-memory caches so next reads pick up fresh data
            self._cache.clear()
            self._data = None
            self._transitions_data = None
            self._last_loaded = None

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
