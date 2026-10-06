"""Individual reports prepared from the same retained submissions as Daily Report."""
from datetime import date, timedelta

from slicing_dashboard.management.periods import today_iso
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
    report_date = report_date or today_iso()
    with manager.refresh_scope():
        daily = manager.get_daily_report_data(report_date, force_refresh=force_refresh)
        periods = manager.get_available_periods()
        current_period = next((p for p in periods if p.get('is_current')), None)
        earliest = manager._daily_report_start_date(report_date) or report_date
        # Include 30 completed days for averages in addition to today's point.
        start = min(earliest, date_range(report_date, 31)[0])
        if current_period and current_period['start_date'] <= report_date:
            start = min(start, current_period['start_date'])
        records = manager._load_daily_report_records(start, report_date)
        canonical = manager._get_reporting_name
        days = {day: day_for_ui(record, day, canonical) for day, record in records.items()
                if start <= day <= report_date}
        # Keep live errors/stale metadata from this exact refresh.
        days.update({day['date']: day for day in daily['history']})
        users = report_users(manager.user_mapping, getattr(manager, '_snapshot_payload', {}),
                             list(days.values()), list(getattr(manager, 'user_mapping_full', {}).values()))
        return json_safe({**daily, 'users': users, 'days': days,
                          'current_period': current_period, 'earliest_recorded': earliest})


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
            'partial': len(recorded) != count or end == today_iso(),
            'stale': any(day['metadata'].get('error') or day['metadata'].get('is_snapshot')
                         or day['metadata'].get('persistence_error') for day in recorded)}


def _completed_values(days, user, end, count):
    if end >= today_iso():
        return None
    values = []
    for key in date_range(end, count):
        day = days.get(key, unavailable_day(key))
        summary = summarize_daily(day, [user])
        if not summary['available'] or day['metadata'].get('error') or day['metadata'].get('is_snapshot'):
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
    return {'user': user, 'report_date': end, 'today': current, 'yesterday': prior,
            'summary': summarize_daily(current), 'previous_summary': summarize_daily(prior),
            'change': day_over_day(current, prior), 'trend': trend,
            'period': period_scope, 'overall': overall,
            'average_7': sum(value for _, value in weekly) / 7 if weekly else None,
            'average_30': sum(value for _, value in monthly) / 30 if monthly else None,
            'best_day': max(monthly, key=lambda pair: pair[1]) if monthly else None}
