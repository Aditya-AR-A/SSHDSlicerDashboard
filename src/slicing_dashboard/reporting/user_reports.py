"""Individual reports prepared from the same retained submissions as Daily Report."""
from datetime import date, timedelta
from copy import deepcopy
import json
from time import perf_counter

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.reporting.completed_work import completed_work_for_period
from slicing_dashboard.reporting.dashboard_reports import (
    date_range, day_for_ui, day_over_day, json_safe, report_users,
    summarize_daily, unavailable_day,
)


def resolve_report_user(selection, users, aliases=None):
    """Resolve selection independently of dropdown, future URL, or login identity."""
    if selection is None:
        return None
    names = {name.casefold(): name for name in users}
    candidate = (aliases or {}).get(selection, selection)
    return names.get(str(candidate).strip().casefold())


def get_user_report_data(manager, report_date=None, force_refresh=False):
    """Prepare chart-sized saved history; live completion loads independently."""
    report_date = report_date or today_iso()
    _validate_date(report_date)
    periods = manager.get_available_periods()
    current_period = next((p for p in periods if p.get('is_current')), None)
    key = (report_date, today_iso(), json.dumps([manager.user_mapping,
           getattr(manager, 'user_mapping_full', {}), periods], sort_keys=True))
    cache = getattr(manager, '_user_report_cache', {})
    entry = cache.get(key)
    from slicing_dashboard.reporting.dashboard_trend import CACHE_SECONDS, bounded_read
    if entry and not force_refresh and perf_counter() - entry[0] < CACHE_SECONDS:
        return deepcopy(entry[1])
    records, earliest = bounded_read(lambda: _saved_history(manager, report_date, current_period))
    from slicing_dashboard.reporting.chart_projections import remap_daily_chart
    canonical = manager._get_reporting_name
    days = {}
    for day, record in records.items():
        if day > report_date:
            continue
        if 'chart_rows' in record:
            record = {**record, 'rows': remap_daily_chart(record['chart_rows'], canonical)}
        days[day] = day_for_ui(record, day, canonical)
    dates = date_range(report_date)
    history = [days.get(day, unavailable_day(day)) for day in dates]
    users = report_users(manager.user_mapping, getattr(manager, '_snapshot_payload', {}),
                         list(days.values()), list(getattr(manager, 'user_mapping_full', {}).values()))
    batches_master = getattr(manager, '_batches_master_cache', {})
    raw_batches = list(batches_master.values()) if isinstance(batches_master, dict) else batches_master
    day_batches = [{**batch, 'canonical_user': canonical(batch.get('assignee_id'), batch['username'])
                    if batch.get('username') else batch.get('canonical_user')}
                   for batch in raw_batches or [] if isinstance(batch, dict) and batch.get('batch_date') == report_date]
    payload = json_safe({'report_date': report_date, 'previous_date': dates[-2],
                        'range_start': dates[0], 'range_end': report_date,
                        'today': history[-1], 'yesterday': history[-2], 'history': history,
                        'users': users, 'days': days, 'current_period': current_period,
                        'earliest_recorded': earliest, 'batches': day_batches,
                        'completed_period': {'loading': True}})
    manager._user_report_cache = {key: (perf_counter(), deepcopy(payload))}
    return payload


def _validate_date(value):
    if date.fromisoformat(value).isoformat() != value or value > today_iso():
        raise ValueError('Invalid user report date')


def _saved_history(manager, end, period):
    from slicing_dashboard.config import DATA_DIR
    from slicing_dashboard.reporting.dashboard_trend import work_records
    retained = {**getattr(manager, '_dashboard_history_records', {}),
                **getattr(manager, '_daily_report_records', {})}
    local = {path.stem for path in (DATA_DIR / 'reports' / 'daily-work').glob('*.json')}
    local.update(path.parent.name.removeprefix('daily-audit-') for path in
                 (DATA_DIR / 'reports').glob('daily-audit-*/verified-submissions.json'))
    dates = set(retained) | local
    try:
        earliest = manager.db.daily_chart_start_date(end)
        if earliest:
            dates.add(earliest)
    except Exception:
        pass  # The subsequent bounded read labels retained fallback evidence.
    valid = []
    for day in dates:
        try:
            if date.fromisoformat(day).isoformat() == day and day <= end:
                valid.append(day)
        except (ValueError, TypeError):
            pass
    earliest = min(valid) if valid else end
    # Include calendar and completed-day average windows, plus all older records
    # needed for the overall total. Local fallback reads only existing files.
    start = min(earliest, date_range(end, 90)[0], period['start_date'] if period else end)
    records = work_records(manager, start, end, local_dates=[day for day in valid if day in local])
    return records, earliest


def get_user_completion_data(manager, report_date=None, force_refresh=False):
    """Keep the exact settlement API read off the work chart's dependency path."""
    report_date = report_date or today_iso()
    _validate_date(report_date)
    period = next((p for p in manager.get_available_periods() if p.get('is_current')), None)
    return completed_work_for_period(manager, period['start_date'] if period else None,
        min(report_date, period['end_date']) if period else None,
        force_refresh=force_refresh, persist=False)


def _scope(days, user, start, end):
    if not start or not end or start > end:
        return {'start': start, 'end': end, 'seconds': None, 'recorded_days': 0,
                'calendar_days': 0, 'partial': True, 'stale': False}
    recorded = [day for key, day in days.items() if start <= key <= end
                and day.get('metadata', {}).get('available')]
    count = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return {'start': start, 'end': end,
            'seconds': sum(summarize_daily(day, [user])['total_seconds'] for day in recorded) if recorded else None,
            'recorded_days': len(recorded), 'calendar_days': count,
            'partial': len(recorded) != count or end == today_iso() or
                       any(day['metadata'].get('reconciliation_pending') for day in recorded),
            'stale': any(day['metadata'].get('error') or day['metadata'].get('is_snapshot')
                         or day['metadata'].get('persistence_error') for day in recorded)}


def _completed_values(days, user, end, count):
    if end >= today_iso():
        return None
    values = []
    for key in date_range(end, count):
        day = days.get(key, unavailable_day(key))
        summary = summarize_daily(day, [user])
        if (not summary['available'] or day['metadata'].get('error') or day['metadata'].get('is_snapshot')
                or day['metadata'].get('reconciliation_pending')):
            return None
        values.append((key, summary['total_seconds']))
    return values


def prepare_user_report(data, selection):
    """Pure selection: switching people performs no network or database reads."""
    user = resolve_report_user(selection, data.get('users', []))
    if user is None:
        return None
    end = data['report_date']
    previous = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    days = data['days']
    def selected_day(key):
        day = days.get(key, unavailable_day(key))
        return {**day, 'rows': [row for row in day.get('rows', []) if row.get('User') == user]}
    current, prior = selected_day(end), selected_day(previous)
    period = data.get('current_period')
    period_scope = _scope(days, user, period['start_date'] if period else None,
                          min(end, period['end_date']) if period else None)
    observed_dates = sorted(key for key, day in days.items()
                            if key <= end and day.get('metadata', {}).get('available'))
    overall = _scope(days, user, observed_dates[0] if observed_dates else None, end)
    weekly = _completed_values(days, user, previous, 7)
    monthly = _completed_values(days, user, previous, 30)
    trend = []
    for key in date_range(end):
        summary = summarize_daily(days.get(key, unavailable_day(key)), [user])
        window = _completed_values(days, user, key, 7)
        trend.append({'date': key, **summary,
                      'rolling_mean_seconds': sum(value for _, value in window) / 7 if window else None})
    user_batches = [b for b in data.get('batches', [])
                    if isinstance(b, dict) and b.get('canonical_user') == user]
    completed = data.get('completed_period') or {}
    matching_scope = (completed.get('start') == period_scope['start']
                      and completed.get('end') == period_scope['end']
                      and period_scope['start'] is not None
                      and period_scope['end'] >= period_scope['start'])
    completion_available = bool(completed.get('available') and matching_scope)
    completed_rows = [row for row in completed.get('rows', []) if row.get('User') == user]
    completion = {**completed, 'available': completion_available,
                  'seconds': sum(row['seconds'] for row in completed_rows) if completion_available else None,
                  'tasks': sum(row['tasks'] for row in completed_rows) if completion_available else None}
    return {'user': user, 'report_date': end, 'today': current, 'yesterday': prior,
            'summary': summarize_daily(current), 'previous_summary': summarize_daily(prior),
            'change': day_over_day(current, prior), 'trend': trend,
            'period': period_scope, 'overall': overall, 'completed_period': completion,
            'average_7': sum(value for _, value in weekly) / 7 if weekly else None,
            'average_30': sum(value for _, value in monthly) / 30 if monthly else None,
            'best_day': max(monthly, key=lambda pair: pair[1]) if monthly else None,
            'batches': user_batches}
