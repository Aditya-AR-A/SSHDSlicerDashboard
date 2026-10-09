"""Small derived chart fields alongside unchanged durable source evidence."""
from collections import defaultdict
import math

from slicing_dashboard.reporting.dashboard_reports import aggregate_observations, reporting_user

APPROVAL_FIELDS = ('id', 'source', 'action', 'active', 'is_synthetic', 'event_at',
                   'member', 'stage', 'batch_id', 'cycle', 'batch_video_seconds_at_capture',
                   'provenance', 'display_date')
DAILY_FIELDS = ('Total Tasks', 'New Videos (First Time)', 'Same-day Rework', 'Old Rework',
                'New Tasks', 'Same-day Rework Tasks', 'Old Rework Tasks')


def workflow_chart(kind, payload):
    if kind == 'event':
        return {key: payload.get(key) for key in APPROVAL_FIELDS}
    if kind == 'efficiency-capture':
        return {'date': payload['date'], 'captured_at': payload['captured_at'],
                'items': [{key: item[key] for key in ('user_id', 'username',
                          'completed_duration_seconds', 'completed_count')} for item in payload['items']]}
    return None


def daily_chart_rows(report):
    if 'tasks' not in report:
        return None
    # Keep accounts separate so later alias changes/exclusions can be applied.
    # A reserved person name on an account must not freeze its exclusion before
    # current mappings are applied. The temporary prefix bypasses person filters.
    prefix = 'Account: '
    rows = aggregate_observations(report['tasks'], lambda uid, name: prefix + name if name else '')
    return [{**row, 'User': row['User'][len(prefix):]} for row in rows]


def remap_daily_chart(rows, canonical_name):
    totals, accounts = defaultdict(lambda: defaultdict(float)), defaultdict(set)
    for row in rows:
        raw = row['User']
        user = canonical_name(None, raw)
        if not reporting_user(user):
            continue
        accounts[user].add(raw)
        for field in DAILY_FIELDS:
            value = float(row.get(field) or 0)
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid saved chart metric')
            totals[user][field] += value
    result = []
    for user in sorted(totals, key=str.casefold):
        row = dict(totals[user])
        fresh, same = row['New Videos (First Time)'], row['Same-day Rework']
        row.update(User=user, RawID=','.join(sorted(accounts[user])),
                   **{'Total Duration': fresh + same, 'Reworks': same,
                      'Rework %': f'{same / (fresh + same) * 100:.1f}%' if fresh + same else '0.0%'})
        result.append(row)
    return result
