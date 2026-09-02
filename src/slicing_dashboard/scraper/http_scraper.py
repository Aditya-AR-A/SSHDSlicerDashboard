"""
HTTP-based scraper for the video slicing dashboard.

Uses the discovered REST API (no Selenium required):
- POST /api/auth/login → cookie-based auth
- GET /api/users → user ID-to-name mapping
- GET /api/dashboard/overview?mode=slice → dashboard data with breakdowns and series

The dashboard is a Vue.js SPA backed by a FastAPI server.
All data is returned as structured JSON with durations in seconds.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
import httpx
from slicing_dashboard.config import RAW_DIR, Settings
from slicing_dashboard.models.schemas import ExtractionResult, RawRecord
from slicing_dashboard.scraper.base import BaseScraper


class HTTPScraper(BaseScraper):
    """HTTP-based dashboard scraper using the discovered REST API.

    Extraction flow:
    1. POST /api/auth/login with {"account": ..., "password": ...}
    2. GET /api/users to map user_id → username
    3. GET /api/dashboard/overview?mode=slice&start_date=...&end_date=...
    4. Parse breakdowns.slice_user_breakdown for per-user stats
    5. Parse series for daily time series
    """

    def __init__(self, settings: (Settings | None)=None) ->None:
        super().__init__(settings)
        self._client = httpx.Client(timeout=self.settings.request_timeout,
            follow_redirects=True, headers={'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            })
        self._users: dict[int, dict[str, Any]] = {}
        self._base_url = self.settings.dashboard_url.rstrip('/')

    def login(self) ->bool:
        """Authenticate via POST /api/auth/login.

        The API uses 'account' (not 'username') as the field name.
        On success, sets an HttpOnly cookie 'pipeline_auth' that
        is automatically managed by httpx.
        """
        if not self._validate_credentials():
            return False
        try:
            login_url = f'{self._base_url}/api/auth/login'
            response = self._client.post(login_url, json={'account': self.
                settings.admin_username, 'password': self.settings.
                admin_password})
            if response.status_code == 200:
                user_data = response.json()
                self._authenticated = True
                return True
            elif response.status_code == 422:
                detail = response.json().get('detail', [])
                return False
            else:
                return False
        except httpx.HTTPError as e:
            return False

    def discover(self) ->dict[str, Any]:
        """Discover dashboard structure via API probing.

        Tests all known endpoints and documents their schemas.
        """
        discovery: dict[str, Any] = {'login': {'url':
            f'{self._base_url}/api/auth/login', 'method': 'POST',
            'body_fields': ['account', 'password'], 'auth_mechanism':
            'cookie', 'cookie_name': 'pipeline_auth'}, 'dashboard': {'url':
            f'{self._base_url}/api/dashboard/overview', 'method': 'GET',
            'params': ['mode', 'start_date', 'end_date'], 'modes': ['slice',
            'label']}, 'users': {'url': f'{self._base_url}/api/users',
            'method': 'GET'}, 'extraction_method': 'http',
            'selenium_required': False, 'errors': []}
        try:
            response = self._client.get(self._base_url)
            discovery['dashboard']['reachable'] = True
            discovery['dashboard']['server'] = response.headers.get('server',
                '')
        except httpx.HTTPError as e:
            discovery['errors'].append(f'Dashboard unreachable: {e}')
            discovery['dashboard']['reachable'] = False
        if self._authenticated:
            try:
                me_resp = self._client.get(f'{self._base_url}/api/auth/me')
                discovery['login']['verified'] = me_resp.status_code == 200
            except Exception as e:
                pass
        return discovery

    def extract(self, start_date: (str | None)=None, end_date: (str | None)
        =None, mode: str='slice') ->ExtractionResult:
        """Extract all individual tasks from the slicing task list.

        Fetches:
        1. User list (for ID → username mapping)
        2. All pages of tasks from /api/slice/tasks

        Returns:
            ExtractionResult with all task records as RawRecords.
        """
        if not self._authenticated:
            return ExtractionResult(success=False, errors=['Not authenticated']
                )
        records: list[RawRecord] = []
        errors: list[str] = []
        warnings: list[str] = []
        try:
            self._fetch_users()
            tasks = []
            page = 1
            has_more = True
            while has_more:
                url = f'{self._base_url}/api/slice/tasks'
                params = {'page_size': 200, 'page': page}
                response = self._client.get(url, params=params)
                response.raise_for_status()
                res_data = response.json()
                page_tasks = res_data.get('data', [])
                if not page_tasks:
                    break
                tasks.extend(page_tasks)
                meta = res_data.get('meta', {})
                has_more = meta.get('has_more', False)
                page += 1
            requests_list = []
            req_page = 1
            req_has_more = True
            while req_has_more:
                url = f'{self._base_url}/api/requests'
                params = {'workflow_type': 'slice', 'page_size': 200,
                    'page': req_page}
                response = self._client.get(url, params=params)
                response.raise_for_status()
                res_data = response.json()
                page_reqs = res_data.get('data', [])
                if not page_reqs:
                    break
                requests_list.extend(page_reqs)
                meta = res_data.get('meta', {})
                req_has_more = meta.get('has_more', False)
                req_page += 1
            reviewed_tasks = []
            rev_page = 1
            rev_has_more = True
            while rev_has_more:
                url = f'{self._base_url}/api/review/reviewed-tasks'
                params = {'page_size': 200, 'page': rev_page}
                response = self._client.get(url, params=params)
                response.raise_for_status()
                res_data = response.json()
                page_revs = res_data.get('data', [])
                if not page_revs:
                    break
                reviewed_tasks.extend(page_revs)
                meta = res_data.get('meta', {})
                rev_has_more = meta.get('has_more', False)
                rev_page += 1
            seen_ids = set()
            for task in tasks:
                task_id = task.get('id')
                if task_id in seen_ids:
                    continue
                seen_ids.add(task_id)
                slicer_id = task.get('slicer_id')
                if slicer_id and not task.get('slicer'):
                    task['slicer'] = self._get_username(slicer_id)
                records.append(self._create_raw_record(task, page=1))
                
            for task in reviewed_tasks:
                task_id = task.get('id')
                if task_id in seen_ids:
                    continue
                seen_ids.add(task_id)
                slicer_id = task.get('slicer_id')
                if slicer_id and not task.get('slicer'):
                    task['slicer'] = self._get_username(slicer_id)
                records.append(self._create_raw_record(task, page=1))
                
            self._save_raw_snapshots(tasks, requests_list, reviewed_tasks)
        except Exception as e:
            pass
            error_msg = f'Extraction failed: {e}'
            errors.append(error_msg)
        return ExtractionResult(success=len(records) > 0 and len(errors) ==
            0, records=records, total_records=len(records), pages_extracted
            =page - 1 if 'page' in locals() else 0, extracted_at=datetime.
            now(), errors=errors, warnings=warnings, metadata={
            'total_tasks': len(records), 'total_requests': len(
            requests_list) if 'requests_list' in locals() else 0,
            'total_reviewed_tasks': len(reviewed_tasks) if 'reviewed_tasks' in
            locals() else 0, 'user_count': len(self._users), 'api_version':
            'slice.v2'})

    def close(self) ->None:
        """Close the HTTP client."""
        self._client.close()

    def _fetch_users(self) ->None:
        """Fetch all users from /api/users and build ID → user mapping."""
        try:
            response = self._client.get(f'{self._base_url}/api/users')
            response.raise_for_status()
            data = response.json()
            items = data.get('items', data) if isinstance(data, dict) else data
            self._users = {user['id']: user for user in items if isinstance
                (user, dict) and 'id' in user}
        except Exception as e:
            pass

    def _get_username(self, user_id: int) ->str:
        """Look up username by user ID."""
        user = self._users.get(user_id)
        if user:
            return user.get('username', f'user-{user_id}')
        return f'user-{user_id}'

    def _save_raw_snapshots(self, tasks: list[dict[str, Any]],
        requests_list: list[dict[str, Any]], reviewed_tasks: list[dict[str,
        Any]]) ->None:
        """Save raw responses as timestamped snapshots."""
        pass

    def get_user_breakdown(self, start_date: (str | None)=None, end_date: (
        str | None)=None, mode: str='slice') ->list[dict[str, Any]]:
        """Convenience method: get user breakdown with usernames merged.

        Returns a list of dicts, each containing user stats with
        the username resolved from the user API.
        """
        if not self._authenticated:
            raise RuntimeError('Not authenticated — call login() first')
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            from datetime import timedelta
            start_dt = datetime.now() - timedelta(days=30)
            start_date = start_dt.strftime('%Y-%m-%d')
        self._fetch_users()
        overview = self._fetch_overview(start_date, end_date, mode)
        if overview is None:
            return []
        breakdown = overview.get('breakdowns', {}).get(f'{mode}_user_breakdown'
            , [])
        result = []
        for entry in breakdown:
            user_id = entry.get('user_id')
            user_info = self._users.get(user_id, {}) if user_id else {}
            result.append({**entry, 'username': user_info.get('username',
                '(unassigned)') if user_id else '(unassigned)', 'role':
                user_info.get('role'), 'group_name': user_info.get(
                'group_name', '')})
        return result

    def get_daily_series(self, start_date: (str | None)=None, end_date: (
        str | None)=None, mode: str='slice') ->list[dict[str, Any]]:
        """Convenience method: get daily time series data."""
        if not self._authenticated:
            raise RuntimeError('Not authenticated — call login() first')
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            from datetime import timedelta
            start_dt = datetime.now() - timedelta(days=30)
            start_date = start_dt.strftime('%Y-%m-%d')
        overview = self._fetch_overview(start_date, end_date, mode)
        if overview is None:
            return []
        return overview.get('series', [])
