"""Batch proportions use identified batches, never estimates from video hours."""
from collections import defaultdict
from datetime import date

import pandas as pd

from slicing_dashboard.processing.daily_work import instant
from slicing_dashboard.processing.pending_review import number


def batch_ratios(batches, returns, start, end, resolve):
    date.fromisoformat(start)
    date.fromisoformat(end)
    recorded, active_returns = defaultdict(set), set()
    for row in returns:
        batch = row.get('batch_id')
        stamp = instant(row.get('returned_at'))
        if not batch or not stamp:
            continue
        identity = row.get('event_id') or (batch, stamp.isoformat(), row.get('user_id'))
        if stamp.date().isoformat() <= end:
            recorded[batch].add(identity)
        if start <= stamp.date().isoformat() <= end:
            active_returns.add(batch)
    totals = {}
    seen = set()
    for batch in batches:
        identifier = batch.get('batch_id')
        if not identifier or identifier in seen:
            raise ValueError('Missing or duplicate batch identity')
        seen.add(identifier)
        day = str(batch.get('batch_date') or '')
        if not (start <= day <= end or identifier in active_returns):
            continue
        status = batch.get('status', '')
        submitted = (status in {'batch_completed', 'batch_rework', 'batch_reviewing',
                     'batch_pending_leader_review', 'batch_pending_auditor_review', 'batch_pending_admin_review'}
                     or any(number(batch.get(field, 0), count=True) > 0 for field in
                            ('completed_count', 'pending_review_count', 'return_count', 'rework_count'))
                     or identifier in active_returns)
        if not submitted:
            continue
        user = resolve(batch)
        if not user or str(user).casefold() in {'admin', 'test', 'dep', 'exempt', 'user-none', '(unassigned)'}:
            continue
        total = totals.setdefault(user, {'tiers': [0] * 6, 'count': 0, 'seconds': 0.})
        total['count'] += 1
        total['seconds'] += number(batch.get('total_duration_seconds'))
        total['tiers'][min(5, len(recorded[identifier]))] += 1
    rows = []
    for user, total in sorted(totals.items()):
        hours = total['seconds'] / 3600
        rows.append({'User': user, **dict(zip(['No Rework', '1 Rework', '2 Reworks', '3 Reworks', '4 Reworks', '5+ Reworks'], total['tiers'])),
                     'Total Batches': total['count'], 'Total Duration (hrs)': hours,
                     'Avg Batch Duration (hrs)': hours / total['count']})
    return pd.DataFrame(rows)
