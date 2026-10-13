"""Date-scoped batch inventory, independent of work charts and completion reads."""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from math import isclose
import logging
from time import perf_counter

from slicing_dashboard.processing.daily_work_source import DailyWorkSource
from slicing_dashboard.processing.source_policy import SOURCE_TTL_SECONDS, serialized_source
from slicing_dashboard.reporting.dashboard_trend import bounded_read
from slicing_dashboard.reporting.user_reports import _validate_date


def worked_batch_ids(tasks, inventory, batches):
    """Match explicit IDs first; otherwise require a unique account inventory fingerprint.

    Overview exposes legacy numbers, while dashboard batches expose assignment IDs.
    A complete account inventory supplies count/duration for each legacy number.
    Ambiguous or incomplete fingerprints never select another batch.
    """
    worked = {(task.get('username'), str(task.get('slice_batch'))) for task in tasks
              if not any(task.get(field) for field in ('assignment_batch_id', 'batch_id'))}
    ids = {task[field] for task in tasks for field in ('assignment_batch_id', 'batch_id') if task.get(field)}
    groups = defaultdict(lambda: [0, 0.])
    for task in inventory:
        key = (task.get('slicer'), str(task.get('slice_batch')))
        groups[key][0] += 1
        groups[key][1] += float(task.get('duration_seconds') or 0)
    unresolved = []
    known_ids = {batch.get('batch_id') for batch in batches}
    unresolved.extend({'username': task.get('username'), 'batch_id': task[field]}
                      for task in tasks for field in ('assignment_batch_id', 'batch_id')
                      if task.get(field) and task[field] not in known_ids)
    for account, legacy in worked:
        if legacy in ('None', ''):
            continue
        direct = [b for b in batches if b.get('username') == account
                  and b.get('legacy_batch_number') is not None and str(b['legacy_batch_number']) == legacy]
        totals = groups.get((account, legacy))
        candidates = direct or [b for b in batches if b.get('username') == account and totals
            and int(b.get('task_count') or 0) == totals[0]
            and isclose(float(b.get('total_duration_seconds') or 0), totals[1], abs_tol=.01)]
        # An identical second legacy inventory would make even a single matching
        # ledger row ambiguous. Preserve the unresolved reference instead.
        twins = [key for key, values in groups.items() if key[0] == account and totals
                 and values[0] == totals[0] and isclose(values[1], totals[1], abs_tol=.01)]
        if len(candidates) == 1 and (direct or len(twins) == 1):
            ids.add(candidates[0]['batch_id'])
        else:
            unresolved.append({'username': account, 'legacy_batch_number': legacy})
    return ids, unresolved


@serialized_source
def get_user_batches_data(manager, report_date, force_refresh=False):
    _validate_date(report_date)
    cache = getattr(manager, '_user_batches_cache', {})
    entry = cache.get(report_date)
    if entry and not force_refresh and perf_counter() - entry[0] < SOURCE_TTL_SECONDS:
        return deepcopy(entry[1])
    try:
        scraper = manager.scraper
        if not scraper.is_authenticated and not scraper.login():
            raise ConnectionError('Batch inventory login failed')
        source = DailyWorkSource(scraper)
        from slicing_dashboard.config import DATA_DIR
        records = [getattr(manager, '_daily_report_records', {}).get(report_date, {})]
        path = DATA_DIR / 'reports' / 'daily-work' / f'{report_date}.json'
        if path.exists():
            records.append(json.loads(path.read_text(encoding='utf-8')))
        records.extend(bounded_read(lambda: manager.db.load_daily_work_reports(report_date, report_date)))
        work_available = any('tasks' in record and record.get('metadata', {}).get('available') is not False
                             for record in records)
        tasks = {str(t['task_id']): t for record in records for t in record.get('tasks', [])}
        accounts = sorted({t['username'] for t in tasks.values() if t.get('username')})
        # The API accepts at most 90 days. Extend only for older worked-account
        # ledger dates, keeping every individual query within that limit.
        start = date.fromisoformat(report_date) - timedelta(days=89)
        known = getattr(manager, '_batches_master_cache', {})
        older = [date.fromisoformat(b['batch_date']) for b in known.values()
                 if b.get('username') in accounts and b.get('batch_date') and b['batch_date'] < start.isoformat()]
        earliest = min(older, default=start)
        payload = source.request('/api/dashboard/batches', params={
            'start_date': start.isoformat(), 'end_date': report_date, 'workflow_type': 'slice'})
        batches = payload.get('breakdowns', {}).get('batches')
        if not isinstance(batches, list) or payload.get('metrics', {}).get('batch_count') != len(batches):
            raise ValueError('Incomplete batch inventory')
        stop = start - timedelta(days=1)
        while stop >= earliest:
            lower = max(earliest, stop - timedelta(days=89))
            extra = source.request('/api/dashboard/batches', params={
                'start_date': lower.isoformat(), 'end_date': stop.isoformat(), 'workflow_type': 'slice'})
            extra_rows = extra.get('breakdowns', {}).get('batches')
            if not isinstance(extra_rows, list) or extra.get('metrics', {}).get('batch_count') != len(extra_rows):
                raise ValueError('Incomplete historical batch inventory')
            batches = batches + extra_rows
            stop = lower - timedelta(days=1)
        if not scraper._users:
            scraper._fetch_users()
        rows = []
        for batch in batches:
            saved = known.get(batch['batch_id'], {})
            username = scraper._get_username(batch.get('assignee_id'))
            # Capture the assignment date from saved evidence or source timestamps;
            # a separate exact-day response identifies new rows without guessing.
            rows.append({**saved, **batch, 'username': username})
        today = source.request('/api/dashboard/batches', params={
            'start_date': report_date, 'end_date': report_date, 'workflow_type': 'slice'})
        assigned = {b['batch_id'] for b in today.get('breakdowns', {}).get('batches', [])}
        if today.get('metrics', {}).get('batch_count') != len(assigned):
            raise ValueError('Incomplete assigned batch inventory')
        inventory = []
        if accounts:
            body = {'page': 1, 'page_size': source.TASK_PAGE_SIZE, 'slicers': accounts}
            group = payload.get('scope', {}).get('group_id')
            if group is not None:
                body['group_ids'] = [group]
            first = source.request('/api/tasks/overview', body=body)
            # Preserve the complete original rows for account/legacy fingerprints.
            source._minimal_task = lambda task: task
            if source._supports_offsets(first):
                try:
                    inventory = source._parallel_inventory(body, first)
                except ValueError:
                    # Task creation can move offset boundaries during a read.
                    # Discard every partial page and start a fresh cursor capture.
                    inventory = source._cursor_inventory(body)
            else:
                inventory = source._cursor_inventory(body, first)
        worked, unresolved = worked_batch_ids(list(tasks.values()), inventory, rows)
        selected = [{**row, 'batch_date': report_date if row['batch_id'] in assigned else row.get('batch_date'),
                     'report_scope': 'Assigned and worked' if row['batch_id'] in assigned & worked else
                                     'Assigned' if row['batch_id'] in assigned else 'Worked'}
                    for row in rows if row['batch_id'] in assigned | worked]
        result = {'report_date': report_date, 'available': True, 'batches': selected,
                  'unresolved': unresolved, 'work_available': work_available,
                  'captured_at': datetime.now(timezone.utc).isoformat()}
        manager._user_batches_cache = {report_date: (perf_counter(), deepcopy(result))}
        return result
    except Exception as error:
        logging.getLogger(__name__).warning('User batch capture failed: %s', error)
        return {'report_date': report_date, 'available': False, 'batches': [], 'error': type(error).__name__}
