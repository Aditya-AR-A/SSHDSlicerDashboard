"""Exact-range completion snapshots, independent of daily submission totals."""
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite

from slicing_dashboard.reporting.dashboard_reports import EXCLUDED_NAMES
from slicing_dashboard.processing.source_policy import SOURCE_TTL_SECONDS, serialized_source


def _number(value, *, count=False):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing completion metric')
    number = float(value)
    if not isfinite(number) or number < 0 or (count and not number.is_integer()):
        raise ValueError('Invalid completion metric')
    return int(number) if count else number


def _fetch_items(manager, start, end):
    items, identities, expected = [], set(), None
    for page in range(1, 51):
        response = manager.scraper._client.get(
            f'{manager.scraper._base_url}/api/dashboard/annotator-efficiency',
            params={'start_date': start, 'end_date': end, 'role': 2,
                    'page': page, 'page_size': 200}, timeout=5.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('Incomplete completion response')
        rows = payload['items']
        total = payload.get('total')
        if total is not None:
            total = _number(total, count=True)
            if expected is not None and total != expected:
                raise ValueError('Completion pagination changed')
            expected = total
        for row in rows:
            if not isinstance(row, dict) or not row.get('username'):
                raise ValueError('Missing completion account')
            identity = (str(row.get('user_id')), row['username'])
            if identity in identities:
                raise ValueError('Repeated completion page')
            identities.add(identity)
            _number(row.get('completed_duration_seconds'))
            _number(row.get('completed_count'), count=True)
        items.extend(rows)
        if expected is not None and len(items) >= expected:
            if len(items) != expected:
                raise ValueError('Inconsistent completion total')
            return items
        if not rows or len(rows) < 200:
            if expected is not None and len(items) != expected:
                raise ValueError('Incomplete completion pagination')
            return items
    raise ValueError('Completion pagination limit reached')


@serialized_source
def completed_work_for_period(manager, start, end, force_refresh=False, *, persist=True):
    """Reuse one minute of data; failed reads only fall back to this exact range."""
    empty = {'start': start, 'end': end, 'rows': [], 'available': False,
             'is_snapshot': False, 'captured_at': None}
    if not start or not end or start > end:
        return empty
    key = f'completed_report_{start}_{end}'
    cache = manager._cache
    saved = cache.get(key)
    now = datetime.now(timezone.utc)
    recent = False
    if isinstance(saved, dict) and saved.get('start') == start and saved.get('end') == end:
        try:
            capture = datetime.fromisoformat(saved['captured_at'])
            recent = capture.tzinfo is not None and 0 <= (now - capture).total_seconds() < SOURCE_TTL_SECONDS
        except (KeyError, TypeError, ValueError):
            pass
    else:
        saved = None
    error = None
    if force_refresh or not recent:
        try:
            if not manager.scraper.is_authenticated and not manager.scraper.login():
                raise ConnectionError('Login failed')
            items = _fetch_items(manager, start, end)
            saved = {'start': start, 'end': end, 'items': items, 'captured_at': datetime.now(timezone.utc).isoformat()}
            cache[key] = saved
            if persist:
                manager._save_snapshot()
        except Exception as failure:
            # Do not expose HTTP messages, credentials, or another period's cache.
            error = type(failure).__name__
    if saved is None:
        return {**empty, 'error': error}
    totals = {}
    try:
        if not isinstance(saved.get('items'), list):
            raise ValueError('Invalid saved completion snapshot')
        for row in saved['items']:
            user = manager._get_reporting_name(row.get('user_id'), row.get('username'))
            if not user or str(user).casefold() in EXCLUDED_NAMES:
                continue
            values = totals.setdefault(user, {'User': user, 'seconds': 0, 'tasks': 0})
            values['seconds'] += _number(row.get('completed_duration_seconds'))
            values['tasks'] += _number(row.get('completed_count'), count=True)
    except (KeyError, TypeError, ValueError, AttributeError):
        return {**empty, 'error': 'InvalidSavedCapture'}
    return {**empty, 'rows': deepcopy(list(totals.values())), 'available': True,
            'captured_at': saved.get('captured_at'), 'is_snapshot': error is not None, 'error': error}
