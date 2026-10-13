"""Recorded submissions and dated batch approvals with explicit duration estimates."""
import math
from slicing_dashboard.processing.daily_work import instant, EXCLUDED
from slicing_dashboard.reporting.dashboard_reports import summarize_daily


APPROVAL_STAGES = (
    ('leader_seconds', 'Leader Approved'),
    ('auditor_seconds', 'Auditor Approved'),
    ('admin_seconds', 'Admin Approved/Completed'),
)


def approval_records(events, batches=(), checkpoints=()):
    """Compact persisted actions for pure browser-side selection/rendering.

    Batch totals attribute video hours to a review event; they are estimates
    from capture-time snapshots. Task reviews and notices cannot multiply them.
    """
    durations = {row.get('batch_id'): row.get('total_duration_seconds') for row in batches}
    rows, seen = [], set()
    for event in sorted(events, key=lambda row: (row.get('event_at') or '', row.get('id') or '')):
        if (event.get('source') != 'batch-review' or event.get('action') != 'Approved'
                or not event.get('active') or event.get('is_synthetic') or not event.get('event_at')
                or event.get('member') in EXCLUDED
                or event.get('stage') not in ('Leader', 'Auditor', 'Admin')):
            continue
        stamp = instant(event['event_at'])
        if not stamp:
            continue
        key = (event.get('batch_id'), event.get('cycle'), event['stage'])
        if key in seen:
            continue
        seen.add(key)
        value = event.get('batch_video_seconds_at_capture')
        if value is None:
            value = durations.get(event.get('batch_id'))
        try:
            value = float(value)
            if not math.isfinite(value) or value < 0:
                value = None
        except (ValueError, TypeError):
            value = None
        rows.append({'date': stamp.date().isoformat(), 'member': event.get('member'),
                     'stage': event['stage'], 'seconds': value,
                     'inferred': event.get('provenance') == 'Stage inferred'})
    checkpoint = next((row for row in checkpoints if row.get('source') == 'batch-review'), {})
    captured = instant(checkpoint.get('last_complete_at'))
    complete = bool(captured and checkpoint.get('complete') is not False
                    and not checkpoint.get('limited') and not checkpoint.get('error'))
    return {'rows': rows, 'coverage_start': min((row['date'] for row in rows), default=None),
            'coverage_end': captured.date().isoformat() if complete else None,
            'complete': complete, 'error': checkpoint.get('error')}


def prepare_approval_trend(data, selected_users=None, approvals=None):
    """Pure preparation: person/theme changes need no additional source reads."""
    rows = []
    for day in data.get('history', []):
        summary = summarize_daily(day, selected_users)
        rows.append({'date': day['date'], 'total_seconds': summary['total_seconds'],
                     **{field: None for field, _ in APPROVAL_STAGES}})
    approvals = approvals or {}
    selected = set(selected_users) if selected_users else None
    lookup = {row['date']: row for row in rows}
    efficiency = data.get('efficiency_history')
    if isinstance(efficiency, list) and efficiency:
        for row in rows:
            row['completed_seconds'] = None
        for day in efficiency:
            if day['date'] in lookup and day.get('metadata', {}).get('available'):
                lookup[day['date']]['completed_seconds'] = sum(
                    float(item['completed_duration_seconds']) for item in day['rows']
                    if selected is None or item['User'] in selected)
    unknown, inferred, dated = 0, 0, 0
    stage_fields = {'Leader': 'leader_seconds', 'Auditor': 'auditor_seconds', 'Admin': 'admin_seconds'}
    start, end = approvals.get('coverage_start'), approvals.get('coverage_end')
    if approvals.get('complete') and start and end:
        for row in rows:
            if start <= row['date'] <= end:
                for field in stage_fields.values():
                    row[field] = 0.0
    missing = set()
    for event in approvals.get('rows', []):
        if event['date'] not in lookup or (selected and event.get('member') not in selected):
            continue
        row, field = lookup[event['date']], stage_fields[event['stage']]
        dated += 1
        inferred += bool(event.get('inferred'))
        if event.get('seconds') is None:
            unknown += 1
            missing.add((event['date'], field))
        else:
            row[field] = (row[field] or 0.0) + event['seconds']
    for day, field in missing:
        lookup[day][field] = None
    status = ('Approval hours estimate batch video duration at capture, using recorded review dates. '
              'Synthetic approvals without dates are excluded.') if dated else 'Approval history unavailable: no dated batch approvals for this selection and period.'
    if inferred:
        status += f' {inferred} approval stages follow the approval-order business rule.'
    if unknown:
        status += f' {unknown} dated approvals lack batch duration; affected days remain gaps.'
    if approvals.get('rows') and not approvals.get('complete'):
        status += ' Review history is incomplete; unrecorded days remain gaps.'
    if approvals.get('error'):
        status += ' Saved approvals shown; source refresh failed.'
    if isinstance(efficiency, list) and efficiency:
        status += ' Completed video uses source-date current status (Asia/Shanghai), not the approval date.'
    return {
        'rows': rows,
        'recorded_days': sum(row['total_seconds'] is not None for row in rows),
        'calendar_days': len(rows),
        'range_start': data.get('range_start'), 'range_end': data.get('range_end'),
        'captured_at': data.get('today', {}).get('metadata', {}).get('captured_at'),
        'stale': any(day.get('metadata', {}).get('error')
                     or day.get('metadata', {}).get('is_snapshot')
                     or day.get('metadata', {}).get('persistence_error')
                     for day in [*data.get('history', []), *(efficiency if isinstance(efficiency, list) else [])]),
        'approval_status': status,
    }
