"""Current unassigned slicing inventory, independent of report date ranges."""
from datetime import datetime, timezone
from math import isfinite


def number(value, *, count=False):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing pool metric')
    amount = float(value)
    if not isfinite(amount) or amount < 0 or (count and not amount.is_integer()):
        raise ValueError('Invalid pool metric')
    return int(amount) if count else amount


def parse_inventory(payload):
    if (not isinstance(payload, dict) or payload.get('workflow_type') != 'slice'
            or payload.get('pool_scope') != 'unassigned'
            or payload.get('pending_status') != 'slice_pending_assign'):
        raise ValueError('Unexpected pool inventory scope')
    pools = {}
    for name in ('normal', 'urgent'):
        row = payload.get(name)
        if not isinstance(row, dict):
            raise ValueError('Incomplete pool inventory')
        pools[name] = {'task_count': number(row.get('task_count'), count=True),
                       'duration_hours': number(row.get('duration_hours'))}
    seconds = number(sum(row['duration_hours'] for row in pools.values()) * 3600)
    return {'available': True, 'duration_seconds': seconds,
            'task_count': sum(row['task_count'] for row in pools.values()),
            'normal': pools['normal'], 'urgent': pools['urgent'],
            'captured_at': datetime.now(timezone.utc).isoformat(), 'error': None}


def fetch_inventory(scraper):
    # The existing authenticated client owns its cookies. No browser session
    # cookie is copied into configuration, source files or saved snapshots.
    if not scraper.is_authenticated and not scraper.login():
        raise ConnectionError('Pool login failed')
    url = f'{scraper._base_url}/api/slice/pool-inventory'
    response = scraper._client.get(url, headers={'Cache-Control': 'no-cache'}, timeout=5.0)
    if response.status_code in (401, 403):
        if not scraper.login():
            raise ConnectionError('Pool session expired')
        response = scraper._client.get(url, headers={'Cache-Control': 'no-cache'}, timeout=5.0)
    response.raise_for_status()
    return parse_inventory(response.json())
