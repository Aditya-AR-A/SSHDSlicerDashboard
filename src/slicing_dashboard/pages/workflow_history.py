"""Workflow history and a compact persistent shared inbox."""
from dash import dcc, html, dash_table
import dash_bootstrap_components as dbc

from slicing_dashboard.pages.components import report_section
from slicing_dashboard.reporting.workflow_history import local_time, notice_link, event_table_rows
from slicing_dashboard.processing.daily_work import instant
from datetime import datetime, timezone, timedelta


def notification_updates(data, seen=None, now=None):
    """Suppress initial history replay, but show newly detected recent events."""
    if not data or data.get('error') or 'inbox' not in data:
        return [], seen
    now = now or datetime.now(timezone.utc)
    rows = data['inbox'].get('rows', [])
    identifiers = [row['id'] for row in rows]
    if not seen:
        return [], {'ids': identifiers, 'since': now.isoformat()}
    prior = set(seen.get('ids', []))
    since = instant(seen['since'])
    updates = []
    for row in rows:
        detected = instant(row.get('observed_at'))
        event_time = instant(row.get('event_at'))
        if (row['id'] not in prior and not row.get('is_read') and detected and detected >= since
                and (event_time is None or event_time >= since - timedelta(minutes=2))):
            updates.append(row)
    return updates, {'ids': list(dict.fromkeys(identifiers + seen.get('ids', [])))[:2000],
                     'since': seen['since']}


def notification_time(notice):
    value = notice.get('event_at') or notice.get('observed_at')
    stamp = instant(value)
    if not stamp:
        return 'Time unavailable'
    seconds = max(0, (datetime.now(timezone.utc) - stamp).total_seconds())
    relative = ('Just now' if seconds < 60 else f'{int(seconds // 60)}m ago' if seconds < 3600
                else f'{int(seconds // 3600)}h ago' if seconds < 86400 else f'{int(seconds // 86400)}d ago')
    return relative if notice.get('event_at') else 'Recorded ' + relative.lower()


def notification_toasts(notices):
    return [dbc.Toast([
        html.P(f"{notice.get('member') or 'Member'} · {notice.get('actor') or 'Reviewer unknown'}", className='mb-1'),
        html.P(notice.get('reason') or '', className='small mb-2'),
        dbc.Button('View report' if notice.get('notification_type') == 'refresh_failed' else 'View batch', id={'type': 'workflow-toast-notice', 'id': notice['id']},
                   n_clicks=0, size='sm', color=notice['category']),
    ], header=notice['title'], icon=notice['category'], dismissable=True,
       is_open=True, duration=10000, className='workflow-toast') for notice in notices[:3]]


def layout_workflow_history(today):
    return html.Div([
        html.Div([html.H1('Workflow History'), html.P('Batch approvals, returns and recorded notices · Asia/Kolkata')],
                 className='report-header'),
        html.P('Synthetic rows fill missing prerequisites under the completion rule. '
               'Unknown review times and reviewer identities remain unknown. '
               'Returns and repeated observed cycles are retained.', className='text-secondary small'),
        html.Div([
            html.Div([html.Label('Member', htmlFor='workflow-member'),
                      dcc.Dropdown(id='workflow-member', placeholder='All members', clearable=True)]),
            html.Div([html.Label('Batch', htmlFor='workflow-batch'), dbc.Input(id='workflow-batch', placeholder='Batch ID', debounce=True)]),
            html.Div([html.Label('Task', htmlFor='workflow-task'), dbc.Input(id='workflow-task', placeholder='Task ID', debounce=True)]),
            html.Div([html.Label('Stage', htmlFor='workflow-stage'), dcc.Dropdown(id='workflow-stage',
                options=[{'label': value, 'value': value} for value in ('Leader', 'Auditor', 'Admin')], placeholder='All stages')]),
            html.Div([html.Label('Evidence', htmlFor='workflow-evidence'), dcc.Dropdown(id='workflow-evidence',
                options=[{'label': value, 'value': value} for value in ('Observed', 'Stage inferred', 'Synthetic')], placeholder='All evidence')]),
            html.Div([html.Label('Action', htmlFor='workflow-action'), dcc.Dropdown(id='workflow-action',
                options=[{'label': value, 'value': value} for value in ('Approved', 'Returned for Rework', 'Sampling Passed')], placeholder='All actions')]),
            html.Div([html.Label('From', htmlFor='workflow-from'), dbc.Input(id='workflow-from', type='date')]),
            html.Div([html.Label('To', htmlFor='workflow-to'), dbc.Input(id='workflow-to', type='date', max=today)]),
            html.Div([html.Label('Search', htmlFor='workflow-search'), dbc.Input(id='workflow-search',
                placeholder='Batch, task, member, reviewer or reason', debounce=True)]),
        ], className='workflow-filters'),
        html.Div(id='workflow-history-status', className='text-secondary small', **{'aria-live': 'polite'}),
        html.Div(id='workflow-batch-state'),
        report_section('Recorded Actions', html.Div(dash_table.DataTable(
            id='workflow-history-table', data=[], columns=[], page_action='custom', page_current=0, page_size=25,
            page_count=0, sort_action='none', style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'padding': '10px', 'minWidth': '140px', 'maxWidth': '300px',
                        'whiteSpace': 'normal', 'overflowWrap': 'anywhere'},
            style_cell_conditional=[{'if': {'column_id': key}, 'maxWidth': '220px',
                                     'whiteSpace': 'nowrap', 'overflow': 'hidden', 'textOverflow': 'ellipsis'}
                                    for key in ('Reason', 'Cycle')],
            tooltip_duration=None,
            style_data_conditional=[
                {'if': {'filter_query': '{Evidence} = "Synthetic"'}, 'fontStyle': 'italic'},
                {'if': {'filter_query': '{Result} = "Rework"'}, 'borderLeft': '4px solid var(--bs-warning)'},
                {'if': {'filter_query': '{Result} = "Approved"'}, 'borderLeft': '4px solid var(--bs-success)'},
            ],
        ), className='report-table-scroll'),
        'Newest records first. Date filters use the review date when known, otherwise the recorded date. '
        'Use a Batch filter to view all retained cycles. Missing stages are labeled Synthetic.'),
    ], className='report-page workflow-page')


def notification_panel(data):
    if not data:
        return html.P('Loading shared inbox…', className='text-secondary small')
    if data.get('error'):
        return dbc.Alert(data['error'], color='warning')
    inbox = data.get('inbox', {})
    entries = []
    for notice in inbox.get('rows', []):
        title = f"{notice.get('member') or 'Member'} · {notice['title']}"
        batch, task = notice.get('batch_id'), notice.get('task_alias') or notice.get('task_id')
        operational = notice.get('notification_type') == 'refresh_failed'
        reference = ('Scheduled dashboard update' if operational else
                     'Batch …' + str(batch)[-8:] if batch else 'Task ' + str(task) if task else 'Reference unavailable')
        entries.append(dbc.ListGroupItem([
            html.I(className='bi ' + ('bi-exclamation-triangle-fill text-danger' if operational else 'bi-check-circle-fill text-success' if notice['category'] == 'success' else 'bi-arrow-counterclockwise text-warning') + ' me-2',
                   **{'aria-hidden': 'true'}),
            dbc.Badge('Read' if notice.get('is_read') else 'Unread', color='secondary' if notice.get('is_read') else 'primary', className='me-2'),
            html.Strong(title), html.Div(notice.get('reason') or 'No additional message.', className='small mt-1'),
            html.Div(reference, className='small text-secondary', title=str(batch or task or '')),
            html.Div(f"{notification_time(notice)} · {notice.get('stage') or 'Reviewer'}: {notice.get('actor') or 'Identity unavailable'}", className='small text-secondary',
                     title=local_time(notice.get('event_at') or notice.get('observed_at')) + ' India'),
            dbc.Button('View report' if operational else 'View batch' if notice.get('batch_id') else 'View task', id={'type': 'workflow-notice', 'id': notice['id']},
                       n_clicks=0, size='sm', color=notice['category'], outline=True, className='mt-2'),
        ], className='workflow-notification ' + ('notice-read' if notice.get('is_read') else 'notice-unread')))
    return html.Div([
        html.P(f"Shared team inbox · {inbox.get('unread', 0)} unread · latest 10 of {inbox.get('total', 0)}. "
               'Read state applies to everyone.', className='small text-secondary'),
        dbc.ListGroup(entries) if entries else html.P('No recorded notices yet.'),
        dcc.Link('View full workflow history', href='/workflow', className='d-block mt-2'),
    ])


def history_table(data):
    rows = event_table_rows(data['rows'])
    labels = list(rows[0]) if rows else ['Event Time (India)', 'Recorded (India)', 'Member', 'Batch', 'Action', 'Stage', 'Evidence']
    labels = [label for label in labels if label != 'Event ID']
    return rows, [{'name': label, 'id': label} for label in labels]
