"""Individual report layout; rendering and selection do not fetch data."""
from dash import dcc, html
import dash_bootstrap_components as dbc

import pandas as pd
from dash.dash_table.Format import Format, Scheme

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.pages.components import report_section, chart_graph
from slicing_dashboard.pages.daily_report import _day_notice, _metric_card
from slicing_dashboard.plots.tables import create_table
from slicing_dashboard.plots.report_insights import build_activity_calendar
from slicing_dashboard.plots.work_breakdown_donut import build_work_breakdown_donut
from slicing_dashboard.plots.work_trend_chart import build_individual_work_chart
from slicing_dashboard.processing.daily_work import format_video_seconds
from slicing_dashboard.reporting.user_reports import prepare_user_report


def layout_user_report(today):
    return html.Div([
        html.Div([
            html.Div([html.H1('User Report', className='h3 mb-1'),
                      html.P('Individual output, rework, and recorded progress.', className='text-secondary mb-0')]),
            html.Div([html.Label('Report date', htmlFor='user-report-date'),
                dbc.Input(id='user-report-date', type='date', value=today, max=today, size='sm',
                          className='date-input-custom'),
                dbc.Button('Today', id='user-report-today', size='sm', color='outline-primary'),
                html.Span('India time', className='text-secondary small')], className='report-controls'),
        ], className='report-header'),
        html.Div([html.Label('Team member', htmlFor='user-report-person', className='fw-semibold small mb-2'),
            dcc.Dropdown(id='user-report-person', options=[], value=None, clearable=False,
                         searchable=True, placeholder='Select a team member')], className='user-report-selector'),
        dcc.Store(id='user-date-follows-today', data=True),
        dcc.Loading([dcc.Store(id='user-report-store'),
                     html.Div(id='user-report-content', children='Loading user report…')], type='circle',
                    overlay_style={'visibility': 'visible', 'opacity': .55},
                    custom_spinner=html.Div('Updating · previous report shown', className='chart-updating-label')),
    ], className='report-page user-report')


def _duration(value):
    return '—' if value is None else format_video_seconds(value)


def _scope_note(scope):
    if scope['start'] and scope['end'] and scope['start'] > scope['end']:
        return 'Current period starts after the report date'
    if scope['seconds'] is None:
        return 'No recorded data in this scope'
    prefix = 'Partial · ' if scope['partial'] else ''
    suffix = ' · saved/unverified capture' if scope['stale'] else ''
    return f"{prefix}{scope['recorded_days']} of {scope['calendar_days']} days recorded{suffix}"


def _scope_panel(title, scope, description):
    date_label = (f"Begins {scope['start']}" if scope['start'] and scope['end'] and scope['start'] > scope['end']
                  else f"{scope['start'] or 'Unavailable'} → {scope['end'] or 'Unavailable'}")
    return html.Div([html.H3(title, className='h6'),
        html.Div(_duration(scope['seconds']), className='h4 mb-2'),
        html.P(date_label, className='small mb-1'),
        html.P(_scope_note(scope), className='small mb-1'),
        html.P(description, className='text-secondary small mb-0')], className='glass-panel user-scope-panel')


STATUS_LABELS = {
    'batch_member_assigned': 'Assigned',
    'batch_pending_auditor_review': 'Pending Auditor Review',
    'batch_pending_admin_review': 'Pending Admin Review',
    'batch_reviewing': 'Reviewing',
    'batch_rework': 'Rework',
    'batch_completed': 'Completed',
    'batch_unassigned': 'Unassigned',
}


def _batch_history_table(batches, is_dark, table_id="user-batch-history-table"):
    if not batches:
        return html.P("No batches recorded for this user on the selected date.", className="text-secondary mb-0")

    rows = []
    for b in batches:
        raw_status = b.get("status", "")
        status_display = STATUS_LABELS.get(raw_status, raw_status.replace("batch_", "").replace("_", " ").title())
        dur_sec = float(b.get("total_duration_seconds", 0) or 0)
        clock_dur = format_video_seconds(dur_sec)
        dur_hrs = round(dur_sec / 3600.0, 3)
        rows.append({
            "Batch ID": b.get("batch_id", ""),
            "Account": b.get("username", ""),
            "Status": status_display,
            "Total Duration (h)": dur_hrs,
            "Duration Clock": clock_dur,
            "Tasks": int(b.get("task_count", 0) or 0),
            "Completed": int(b.get("completed_count", 0) or 0),
            "Pending Review": int(b.get("pending_review_count", 0) or 0),
            "Rework": int(b.get("rework_count", 0) or 0),
            "Returns": int(b.get("return_count", 0) or 0),
        })

    columns = [
        {"name": "Batch ID", "id": "Batch ID"},
        {"name": "Account", "id": "Account"},
        {"name": "Status", "id": "Status"},
        {"name": "Total Duration (h)", "id": "Total Duration (h)", "type": "numeric", "format": Format(precision=3, scheme=Scheme.fixed)},
        {"name": "Tasks", "id": "Tasks", "type": "numeric"},
        {"name": "Completed", "id": "Completed", "type": "numeric"},
        {"name": "Pending Review", "id": "Pending Review", "type": "numeric"},
        {"name": "Rework", "id": "Rework", "type": "numeric"},
        {"name": "Returns", "id": "Returns", "type": "numeric"},
    ]
    tooltip_data = [
        {"Total Duration (h)": {"value": row["Duration Clock"], "type": "text"}}
        for row in rows
    ]

    display_rows = [{k: v for k, v in row.items() if k != "Duration Clock"} for row in rows]
    return html.Div(
        create_table(pd.DataFrame(display_rows), is_dark, table_id=table_id, columns=columns,
                     tooltip_data=tooltip_data, max_height="350px"),
        className="report-table-scroll"
    )


def build_user_report_content(data, selected_user, is_dark=True):
    if not data:
        return html.P('Loading user report…', className='text-secondary')
    if data.get('validation_error'):
        return dbc.Alert(data['validation_error'], color='warning')
    report = prepare_user_report(data, selected_user)
    if report is None:
        return dbc.Alert('Choose an available team member to view their report.', color='info')
    current = report['report_date'] == today_iso()
    calendar, recorded_days = build_activity_calendar(data.get('days', {}), report['user'], report['report_date'], is_dark)
    summary, previous, change = report['summary'], report['previous_summary'], report['change']
    change_note = 'Previous-day comparison unavailable'
    if change['difference_seconds'] is not None:
        change_note = f"{change['difference_seconds'] / 3600:+.2f}h vs previous day"
        if change['percentage'] is not None:
            change_note += f" ({change['percentage']:+.1f}%)"
        if current:
            change_note = 'Today so far: ' + change_note
    previous_meta = report['yesterday'].get('metadata', {})
    previous_note = ('No recorded history' if not previous['available'] else 'Saved capture · refresh unverified'
                     if previous_meta.get('error') or previous_meta.get('is_snapshot') else 'Reconciliation pending'
                     if previous_meta.get('reconciliation_pending') else 'Recorded submissions')
    completed = report['completed_period']
    completion_note = 'Completion data unavailable'
    if completed['available']:
        completion_note = f"{completed['tasks']} completed tasks · {completed['start']} → {completed['end']}"
        if completed.get('is_snapshot'):
            completion_note += ' · saved capture; refresh failed'
    items = [
        ("Today's Work" if current else 'Selected-day Work', summary['total_seconds'],
         change_note + ' · new + same-day rework', '#38bdf8', 'bi-activity', True),
        ("Yesterday's Work" if current else 'Previous-day Work', previous['total_seconds'],
         previous_note, '#a78bfa', 'bi-clock-history', False),
        ('Completed Video This Settlement', completed['seconds'], completion_note,
         '#34d399', 'bi-check2-circle', True),
        ('Recorded Work This Settlement', report['period']['seconds'], _scope_note(report['period']),
         '#60a5fa', 'bi-calendar-range', False),
        ('New Work', summary['new_seconds'], 'Fresh submissions included in daily total',
         '#38bdf8', 'bi-file-earmark-plus', False),
        ('Same-day Rework', summary['same_day_rework_seconds'], 'Same-day corrections included in daily total',
         '#fbbf24', 'bi-arrow-repeat', False),
        ('Old Rework', summary['old_rework_seconds'], 'Earlier returns · excluded from daily total',
         '#fb923c', 'bi-exclamation-circle', False),
        ('Overall Recorded Work', report['overall']['seconds'], _scope_note(report['overall']),
         '#c084fc', 'bi-collection', False),
    ]
    cards = []
    for title, value, note, color, icon, featured in items:
        card = _metric_card(title, _duration(value), note, color, icon, featured=featured)
        card.className += ' user-metric-card'
        cards.append(card)
    coverage = sum(row['available'] for row in report['trend'])
    stats = [html.Div([html.Dt('Same-day rework share'), html.Dd(
        '—' if summary['rework_percentage'] is None else f"{summary['rework_percentage']:.1f}%")]),
        html.Div([html.Dt('7-day daily mean'), html.Dd(_duration(report['average_7']))]),
        html.Div([html.Dt('30-day daily mean'), html.Dd(_duration(report['average_30']))]),
        html.Div([html.Dt('Best day in previous 30 days'), html.Dd(
            f"{report['best_day'][0]} · {_duration(report['best_day'][1])}" if report['best_day'] else '—')])]
    return html.Div([
        html.H2(report['user'], className='h4 mb-0'),
        html.Div(cards, className='user-report-kpis'),
        html.Div(_day_notice(report['today']), className='report-status', **{'aria-live': 'polite'}),
        # Dash defaults to inline height:100%; give graphs their own height so
        # the section heading and description do not add overflow below the card.
        html.Div([report_section('90-day Activity Calendar', [
            html.P(f'{recorded_days} of 90 days recorded. Grey cells have no recorded capture; '
                   'blue cells show recorded output, including zero.', className='small text-secondary'),
            chart_graph(id='user-activity-calendar', figure=calendar, responsive=True,
                      style={'height': '360px', 'width': '100%'},
                      config={'responsive': True, 'displayModeBar': False})],
            'Daily submitted video duration through the selected report date. Hover a day for its exact duration.'),
        report_section('30-day Individual Trend', [
            html.P(f'{coverage} of 30 days recorded. Missing dates appear as gaps.', className='small text-secondary'),
            chart_graph(id='user-work-trend', figure=build_individual_work_chart(report['trend'], report['user'], is_dark), responsive=True,
                      style={'height': '360px', 'width': '100%'},
                      className='report-comparison-chart', config={'responsive': True, 'displayModeBar': False})],
            'Use the legend to show work types. A 7-day mean appears only when recorded completed days support it.')],
            className='user-report-trend-grid'),
        html.P(('Completion totals use the source API’s current completed status for the settlement date range. '
                'They are separate from recorded submissions and do not reconstruct historical approval times. '
                + (f"Completion capture: {completed['captured_at']}." if completed.get('captured_at') else '')),
               className='small text-secondary mb-0'),
        html.Div([
            report_section("Today's Work Breakdown" if current else f"Work Breakdown · {report['report_date']}",
                chart_graph(id='user-work-breakdown', figure=build_work_breakdown_donut(summary, is_dark), responsive=True,
                          style={'height': '360px', 'width': '100%'},
                          config={'responsive': True, 'displayModeBar': False}),
                'Total work includes new work and same-day rework. Old rework is excluded and shown separately above.'),
            report_section('Recorded Scope Totals', [
                _scope_panel('Current settlement period', report['period'],
                             'Submitted video duration through the report date; this is not a paid or completed-work balance.'),
                _scope_panel('Overall recorded', report['overall'],
                             'Starts at the earliest retained record. Earlier work is not included.')]),
        ], className='user-report-breakdown-grid'),
        report_section('Supporting Statistics', [html.Dl(stats, className='user-report-statistics'),
            html.P('Means and best day require every day in the preceding 7 or 30 completed calendar days to be recorded. '
                   'Today is excluded. Values measure recorded video output, not labor time.', className='text-secondary small')]),
        report_section(f"Batch History · {report['report_date']}",
                       _batch_history_table(report.get('batches', []), is_dark)),
    ], className='report-content')
