"""Individual report layout; rendering and selection do not fetch data."""
from dash import dcc, html
import dash_bootstrap_components as dbc

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.pages.components import report_section
from slicing_dashboard.pages.daily_report import _day_notice
from slicing_dashboard.plots.kpi_cards import make_kpi_card
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
                     html.Div(id='user-report-content', children='Loading user report…')], type='circle'),
    ], className='report-page')


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


def build_user_report_content(data, selected_user, is_dark=True):
    if not data:
        return html.P('Loading user report…', className='text-secondary')
    if data.get('validation_error'):
        return dbc.Alert(data['validation_error'], color='warning')
    report = prepare_user_report(data, selected_user)
    if report is None:
        return dbc.Alert('Choose an available team member to view their report.', color='info')
    current = report['report_date'] == today_iso()
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
                     if previous_meta.get('error') or previous_meta.get('is_snapshot') else 'Recorded submissions')
    items = [
        ("Yesterday's Work" if current else 'Previous-day Work', previous['total_seconds'], previous_note),
        ("Today's Work" if current else 'Selected-day Work', summary['total_seconds'], change_note),
        ('New Work', summary['new_seconds'], 'Fresh submissions'),
        ('Same-day Rework', summary['same_day_rework_seconds'], 'Returned within the same day'),
        ('Old Rework', summary['old_rework_seconds'], 'Returned on a previous day'),
        ('Current Settlement Period', report['period']['seconds'], _scope_note(report['period'])),
        ('Overall Recorded Work', report['overall']['seconds'], _scope_note(report['overall'])),
    ]
    cards = [make_kpi_card(title, _duration(value), badge_text=note, is_dark=is_dark)
             for title, value, note in items]
    coverage = sum(row['available'] for row in report['trend'])
    stats = [html.Div([html.Dt('Rework share'), html.Dd(
        '—' if summary['rework_percentage'] is None else f"{summary['rework_percentage']:.1f}%")]),
        html.Div([html.Dt('7-day daily mean'), html.Dd(_duration(report['average_7']))]),
        html.Div([html.Dt('30-day daily mean'), html.Dd(_duration(report['average_30']))]),
        html.Div([html.Dt('Best day in previous 30 days'), html.Dd(
            f"{report['best_day'][0]} · {_duration(report['best_day'][1])}" if report['best_day'] else '—')])]
    return html.Div([
        html.H2(report['user'], className='h4 mb-0'),
        html.Div(cards, className='report-kpi-grid user-report-kpis'),
        html.Div(_day_notice(report['today']), className='report-status', **{'aria-live': 'polite'}),
        report_section('30-day Individual Trend', [
            html.P(f'{coverage} of 30 days recorded. Missing dates appear as gaps.', className='small text-secondary'),
            dcc.Graph(id='user-work-trend', figure=build_individual_work_chart(report['trend'], report['user'], is_dark),
                      className='report-comparison-chart', config={'responsive': True, 'displayModeBar': False})],
            'Use the legend to show work types. A 7-day mean appears only when recorded completed days support it.'),
        html.Div([
            report_section("Today's Work Breakdown" if current else f"Work Breakdown · {report['report_date']}",
                dcc.Graph(id='user-work-breakdown', figure=build_work_breakdown_donut(summary, is_dark),
                          config={'responsive': True, 'displayModeBar': False})),
            report_section('Recorded Scope Totals', [
                _scope_panel('Current settlement period', report['period'],
                             'Submitted video duration through the report date; this is not a paid or completed-work balance.'),
                _scope_panel('Overall recorded', report['overall'],
                             'Starts at the earliest retained record. Earlier work is not included.')]),
        ], className='user-report-breakdown-grid'),
        report_section('Supporting Statistics', [html.Dl(stats, className='user-report-statistics'),
            html.P('Means and best day require every day in the preceding 7 or 30 completed calendar days to be recorded. '
                   'Today is excluded. Values measure recorded video output, not labor time.', className='text-secondary small')]),
    ], className='report-content')
