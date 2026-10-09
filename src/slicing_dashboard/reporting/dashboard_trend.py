"""Bounded saved-history reads, independent of interactive task/workflow captures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from time import perf_counter

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.reporting.dashboard_reports import date_range, day_for_ui, report_users

READ_SECONDS = 5
CACHE_SECONDS = 30


def bounded_read(read):
    # CSOT bounds the whole Mongo cursor, including getMore and server selection,
    # rather than allowing each of 30 dates another 15-second timeout.
    from pymongo import timeout
    with timeout(READ_SECONDS):
        return read()


def work_records(manager, start, end, local_dates=None):
    retained = {**getattr(manager, '_dashboard_history_records', {}),
                **getattr(manager, '_daily_report_records', {})}
    failed = False
    try:
        records = bounded_read(lambda: manager.db.load_daily_chart_reports(start, end))
        retained.update({record['date']: record for record in records})
    except Exception:
        failed = True
        retained = {day: {**record, 'metadata': {**record.get('metadata', {}),
                    'is_snapshot': True, 'error': 'DailyChartReadFailed'}}
                    for day, record in retained.items()}
    # A read-only local fallback keeps retained observations visible offline.
    from slicing_dashboard.config import DATA_DIR
    for day in local_dates if local_dates is not None else date_range(end):
        if day in retained:
            continue
        paths = (DATA_DIR / 'reports' / 'daily-work' / (day + '.json'),
                 DATA_DIR / 'reports' / ('daily-audit-' + day) / 'verified-submissions.json')
        for path in paths:
            if not path.exists():
                continue
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if record.get('metadata', {}).get('target_date') == day:
                    record = {**record, 'date': day}
                if record.get('date') == day:
                    if failed:
                        record = {**record, 'metadata': {**record.get('metadata', {}),
                                  'is_snapshot': True, 'error': 'DailyChartReadFailed'}}
                    retained[day] = record
                    break
            except (OSError, ValueError):
                pass
    manager._dashboard_history_records = retained
    return retained


def saved_trend(manager, report_date=None, force_refresh=False):
    end = report_date or today_iso()
    dates = date_range(end)
    if end > today_iso():
        raise ValueError('The chart cannot end after today in India time.')
    key = (end, today_iso(), json.dumps(getattr(manager, 'user_mapping_full', {}), sort_keys=True))
    cache = getattr(manager, '_dashboard_trend_cache', {})
    entry = cache.get(key)
    if not force_refresh and entry and perf_counter() - entry[0] < CACHE_SECONDS:
        return deepcopy(entry[1])
    start = dates[0]
    # These reads are independent. No global daily-payload or workflow capture
    # lock is held while awaiting a remote response.
    with ThreadPoolExecutor(max_workers=3) as pool:
        work = pool.submit(work_records, manager, start, end)
        approvals = pool.submit(bounded_read, lambda: manager.get_approval_data(start, end))
        efficiency = pool.submit(bounded_read, lambda: manager.get_efficiency_history(start, end, capture_live=False))
        records = work.result()
        from slicing_dashboard.reporting.chart_projections import remap_daily_chart
        records = {day: {**record, 'rows': remap_daily_chart(record['chart_rows'], manager._get_reporting_name)}
                   if 'chart_rows' in record else record for day, record in records.items()}
        history = [day_for_ui(records.get(day), day, manager._get_reporting_name) for day in dates]
        payload = {'report_date': end, 'previous_date': dates[-2], 'range_start': start,
                   'range_end': end, 'today': history[-1], 'yesterday': history[-2], 'history': history,
                   'users': report_users(getattr(manager, 'user_mapping', {}), history=history,
                                        mapping_records=list(getattr(manager, 'user_mapping_full', {}).values())),
                   'approvals': approvals.result(), 'efficiency_history': efficiency.result()}
    manager._dashboard_trend_cache = {key: (perf_counter(), deepcopy(payload))}
    return payload
