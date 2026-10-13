"""Saved first paint and independent updates for the dashboard's five panels."""
from uuid import uuid4

import dash
from dash import Input, Output, State, dcc
import dash_bootstrap_components as dbc

from slicing_dashboard.reporting.dashboard_panels import (
    KINDS, capture_panel, saved_panel, panel_frame, selected_summary,
)
from slicing_dashboard.plots import (
    build_kpi_layout, build_individual_chart, build_rework_ratio_chart,
    build_assigned_chart, build_pending_chart,
)
from slicing_dashboard.plots.guardrails import state_figure

GRAPH_IDS = {'completion': 'individual-chart', 'rework': 'rework-ratio-chart',
             'assigned': 'assigned-chart', 'pending': 'pending-chart'}


def panel_stores():
    return [dcc.Store(id='dashboard-panel-request'),
            dcc.Store(id='dashboard-pending-request'),
            *[dcc.Store(id=f'dashboard-{phase}-{kind}')
              for kind in KINDS for phase in ('saved', 'live', 'ready', 'painted')]]


def matching_packet(request, packet):
    return packet if packet and packet.get('request_id') == request.get('id') else None


def choose_packet(request, saved, live):
    saved, live = matching_packet(request, saved), matching_packet(request, live)
    if live and live.get('available'):
        return live, f"Captured {live['captured_at']}"
    if saved and saved.get('available'):
        state = 'Latest check failed' if live else 'Checking latest data'
        return saved, f"Saved {saved['captured_at']} · {state}"
    return None, 'Latest data unavailable. Refresh to retry.' if live else 'Loading latest data…'


def render_panel(kind, request, saved, live, selected, theme, roster):
    dark = (theme or 0) % 2 == 0
    packet, status = choose_packet(request, saved, live)
    if not packet:
        content = (dbc.Alert(status, color='secondary') if kind == 'summary'
                   else state_figure(status, dark))
        return content, status
    if kind == 'summary':
        return build_kpi_layout(selected_summary(packet['data'], selected, roster),
                                packet['data']['funnel'], dark), status
    frame = panel_frame(packet)
    if selected is not None and 'User' in frame:
        frame = frame[frame['User'].isin(selected)]
    if kind == 'completion':
        figure = build_individual_chart(frame, request['start'], request['end'], dark)
    elif kind == 'rework':
        figure = build_rework_ratio_chart(frame, request['start'], dark, None, False)
    elif kind == 'assigned':
        figure = build_assigned_chart(frame, dark)
    else:
        figure = build_pending_chart(frame, dark)
    return figure, status


def register_dashboard_panels(app, manager, roster):
    @app.callback(
        Output('dashboard-panel-request', 'data'),
        Output('dashboard-pending-request', 'data'),
        *[Output(f'dashboard-saved-{kind}', 'data') for kind in KINDS],
        Input('date-from', 'value'), Input('date-to', 'value'),
        Input('refresh-btn', 'n_clicks'), Input('auto-refresh-interval', 'n_intervals'),
        Input('report-location', 'pathname'),
        Input('workflow-notification-interval', 'n_intervals'), Input('workflow-bell', 'n_clicks'),
    )
    def request_panels(start, end, clicks, intervals, pathname, notifications, bell):
        if pathname not in (None, '/', '/dashboard') or not start or not end:
            raise dash.exceptions.PreventUpdate
        trigger = dash.ctx.triggered_id
        request = {'id': uuid4().hex, 'start': start, 'end': end,
                   'force': trigger in ('refresh-btn', 'auto-refresh-interval'),
                   'pending_force': trigger in ('workflow-notification-interval', 'workflow-bell')}
        # Notification checks affect the current review queue only. A new shared
        # request ID would otherwise restart all five panel capture pipelines.
        if request['pending_force']:
            packet = saved_panel(manager, 'pending', start, end) or {'available': False}
            return (dash.no_update, request,
                    *[{**packet, 'request_id': request['id']} if kind == 'pending'
                      else dash.no_update for kind in KINDS])
        saved = []
        for kind in KINDS:
            packet = saved_panel(manager, kind, start, end) or {'available': False}
            saved.append({**packet, 'request_id': request['id']})
        return request, request, *saved

    for kind in KINDS:
        _register_panel(app, manager, roster, kind)


def _register_panel(app, manager, roster, kind):
    request_store = 'dashboard-pending-request' if kind == 'pending' else 'dashboard-panel-request'
    outputs = ([Output('kpi-cards', 'children'), Output('refresh-status', 'children')]
               if kind == 'summary' else
               [Output(GRAPH_IDS[kind], 'figure'), Output(GRAPH_IDS[kind] + '-status', 'children')])

    @app.callback(Output(f'dashboard-live-{kind}', 'data'),
                  Input(f'dashboard-ready-{kind}', 'data'), State(request_store, 'data'))
    def capture(ready, request):
        if not request or not matching_packet(request, ready):
            raise dash.exceptions.PreventUpdate
        packet = capture_panel(manager, kind, request['start'], request['end'],
                               force=request['force'] or (kind == 'pending' and request['pending_force']))
        return {**packet, 'request_id': request['id']}

    # No live-store Input here: Dash would wait for that store's source callback
    # before painting the saved result.
    @app.callback(*outputs, Output(f'dashboard-ready-{kind}', 'data'),
                  Output(f'dashboard-painted-{kind}', 'data'),
                  Input(request_store, 'data'), Input(f'dashboard-saved-{kind}', 'data'),
                  Input('selected-users-store', 'data'), Input('theme-toggle', 'n_clicks'),
                  State(f'dashboard-live-{kind}', 'data'), State('report-location', 'pathname'),
                  State(f'dashboard-ready-{kind}', 'data'))
    def paint_saved(request, saved, selected, theme, live, pathname, ready):
        if not request or pathname not in (None, '/', '/dashboard'):
            raise dash.exceptions.PreventUpdate
        # Start this panel's live read only after its saved/loading response.
        # Otherwise a fast live response can be overwritten by a slower first
        # paint from a different callback, leaving the selected range stale.
        start_capture = dash.no_update if matching_packet(request, ready) else {'request_id': request['id']}
        painted = {'request_id': request['id'], 'id': uuid4().hex}
        return *render_panel(kind, request, saved, live, selected, theme, roster), start_capture, painted

    @app.callback(*[Output(output.component_id, output.component_property, allow_duplicate=True)
                    for output in outputs],
                  Input(f'dashboard-live-{kind}', 'data'),
                  Input(f'dashboard-painted-{kind}', 'data'),
                  State(request_store, 'data'), State(f'dashboard-saved-{kind}', 'data'),
                  State('selected-users-store', 'data'), State('theme-toggle', 'n_clicks'),
                  State('report-location', 'pathname'), prevent_initial_call=True)
    def paint_live(live, painted, request, saved, selected, theme, pathname):
        if not request or not matching_packet(request, live) or pathname not in (None, '/', '/dashboard'):
            raise dash.exceptions.PreventUpdate
        return render_panel(kind, request, saved, live, selected, theme, roster)
