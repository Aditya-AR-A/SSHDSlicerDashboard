"""Read a complete current task queue without settlement/date filters."""
from slicing_dashboard.processing.daily_work_source import DailyWorkSource
from slicing_dashboard.processing.daily_work import instant
from datetime import date as calendar_date


def assignment_metadata(task, batches, returns=()):
    """Attach dates to live quantities; ledger rows never create assignments.

    Prefer an exact batch link or original timestamp. Legacy return history
    can link rework tasks. An account ledger date is usable when its active
    batches share one date; ambiguous dates remain unknown.
    """
    def date(value):
        try:
            if value and len(str(value)) == 10:
                return calendar_date.fromisoformat(str(value)).isoformat()
            stamp = instant(value)
            return stamp.date().isoformat() if stamp else ''
        except (ValueError, TypeError, OverflowError):
            return ''

    direct_date = date(task.get('assigned_at') or task.get('assignment_date'))
    batch_id = task.get('assignment_batch_id') or task.get('batch_id')
    legacy = task.get('slice_batch')
    if not batch_id and legacy is not None:
        linked = {row.get('batch_id') for row in returns
                  if str(row.get('user_id')) == str(task.get('slicer_id'))
                  and str(row.get('legacy_batch_number')) == str(legacy) and row.get('batch_id')}
        if len(linked) == 1:
            batch_id = linked.pop()
    candidates = [row for row in batches if row.get('batch_id') == batch_id] if batch_id else []
    if not candidates and not batch_id:
        candidates = [row for row in batches
                      if row.get('status') in ('batch_member_assigned', 'batch_rework')
                      and ((task.get('slicer') and row.get('username') == task.get('slicer'))
                           or (task.get('slicer_id') is not None
                               and str(row.get('assignee_id')) == str(task['slicer_id'])))]
        # A legacy identifier retained by a newer ledger is an exact link.
        exact = [row for row in candidates if legacy is not None
                 and str(row.get('legacy_batch_number')) == str(legacy)]
        if exact:
            candidates = exact
            batch_id = exact[0].get('batch_id') if len(exact) == 1 else None
    dates = {date(row.get('assigned_at') or row.get('batch_date')) for row in candidates}
    known = direct_date or (next(iter(dates)) if len(dates) == 1 and '' not in dates else '')
    return {'AssignedDate': known, 'BatchID': batch_id or ', '.join(sorted(
                str(row['batch_id']) for row in candidates if row.get('batch_id') and known)),
            'AssignmentDateBasis': ('Assignment timestamp' if direct_date else
                                    'Batch assignment date' if known and batch_id else
                                    'Account batch date' if known else 'Unknown')}


def fetch_queue(scraper, status):
    source = DailyWorkSource(scraper)
    tasks, seen_ids, seen_cursors = [], set(), set()
    page, cursor = 1, None
    while True:
        params = {'status': status, 'workflow_type': 'slice', 'page_size': 200, 'page': page}
        if cursor:
            params['cursor'] = cursor
        payload = source.request('/api/slice/tasks', params=params)
        if not isinstance(payload.get('data'), list):
            raise ValueError('Current assignment inventory response is invalid')
        rows, meta = payload.get('data', []), payload.get('meta', {})
        if meta.get('total_capped'):
            raise ValueError('Current assignment inventory is capped')
        new = 0
        for row in rows:
            if row.get('status') != status:
                continue
            identifier = row.get('id')
            if not identifier:
                raise ValueError('Current assignment inventory has no task identifier')
            if identifier not in seen_ids:
                seen_ids.add(identifier)
                tasks.append(row)
                new += 1
        has_more = meta.get('has_more')
        if has_more is None:
            has_more = page * int(meta.get('page_size') or 200) < int(meta.get('total') or 0)
        if not has_more:
            return tasks
        if not rows or not new:
            raise ValueError('Current assignment inventory pagination did not advance')
        cursor = meta.get('next_cursor')
        if cursor and cursor in seen_cursors:
            raise ValueError('Current assignment inventory repeated a cursor')
        if cursor:
            seen_cursors.add(cursor)
        page += 1
