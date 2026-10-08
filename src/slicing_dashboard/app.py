"""
Slicing Performance & Settlement Dashboard — Main Application.

Layout and callbacks. All chart/table builders live in the plots/ package.
"""

import dash
import sys
import dash_bootstrap_components as dbc
import pandas as pd
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from dash import ALL, Input, Output, State, dcc, html
from datetime import datetime, timedelta
from pathlib import Path

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.plots import (
    USER_COLORS,
    format_seconds,
    empty_fig,
    build_rework_ratio_chart,
    build_individual_chart,
    build_pending_chart,
    build_assigned_chart,
    build_legend_figure,
    build_kpi_layout,
    create_table,
)
from slicing_dashboard.plots.theme import CHART_HEIGHT
from slicing_dashboard.plots.guardrails import state_figure
from slicing_dashboard.management.ui import layout_settlement_management, layout_user_mapping, register_management_callbacks
from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.processing.daily_work import EXCLUDED
from slicing_dashboard.pages.components import add_reporting_shell, contain_graphs
from slicing_dashboard.pages.daily_report import build_daily_report_content
from slicing_dashboard.pages.user_report import build_user_report_content
from slicing_dashboard.reporting.approval_reports import prepare_approval_trend
from slicing_dashboard.plots.approval_trend_chart import build_approval_trend_chart
from slicing_dashboard.pages.workflow_history import notification_panel, history_table, notification_updates, notification_toasts

# ── Bootstrap / initialise ───────────────────────────────────────────────
dm = DataManager()
periods = dm.get_available_periods()
default_period = next((p for p in periods if p.get('is_current')), periods[-1])
default_start = default_period['start_date']
default_end = default_period['end_date']
default_period_value = default_period['value']


app = dash.Dash(
    __name__,
    title="SSHD Slicing Dashboard",
    update_title=None,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    assets_folder=str(Path(__file__).resolve().parent / "assets"),
    suppress_callback_exceptions=True,
)

from slicing_dashboard.reporting.data_health import register_data_health_routes
register_data_health_routes(app.server, dm)

def _available_user_names(snapshot, mappings):
    names = list(snapshot.get("available_users", [])) + list(mappings.values())
    return sorted({name for name in names if name and name not in EXCLUDED})


available_users = _available_user_names(dm._snapshot_payload, dm.user_mapping)
if not available_users:
    available_users = ["Aditya", "Deepak", "Komal", "Pawan", "Priya", "Rajni", "Riya", "Sanddep"]

# ── Chart style shorthand ────────────────────────────────────────────────
_graph_style = {"height": "340px", "width": "100%", "minWidth": 0}
_graph_cfg = {"displayModeBar": False, "responsive": True}
_initial_fig = empty_fig()

# ── Layout ───────────────────────────────────────────────────────────────
app.layout = html.Div(
    id="main-container",
    className="theme-dark",
    children=[
        dbc.Container(
            [
                # ── Header bar ───────────────────────────────────────
                dbc.Row(
                    [
                        dbc.Col(
                            html.Div(
                                [
                                    html.I(
                                        className="bi bi-film text-primary me-2",
                                        style={"fontSize": "1.25rem"},
                                    ),
                                    html.Span(
                                        "SSHD",
                                        className="fw-bold text-primary me-2",
                                        style={"fontSize": "1.15rem", "letterSpacing": "0.5px"},
                                    ),
                                    html.Span(
                                        "Slicing Performance & Settlement",
                                        className="fw-semibold text-body",
                                        style={"fontSize": "1.1rem"},
                                    ),
                                ],
                                className="d-flex align-items-center",
                            ),
                            width="auto",
                            className="me-auto",
                        ),
                        dbc.Col(
                            dbc.ButtonGroup(
                                [
                                    dcc.Dropdown(
                                        id="settlement-dropdown",
                                        options=[{"label": p["label"], "value": p.get("value", f"{p['start_date']}|{p['end_date']}")} for p in periods],
                                        value=default_period_value,
                                        clearable=False,
                                        className="dropdown-glass",
                                        style={"minWidth": "260px", "marginRight": "10px"},
                                    ),
                                    dbc.Button(
                                        "Today",
                                        id="btn-today",
                                        color="outline-primary",
                                        size="sm",
                                        title="Today's performance",
                                    ),
                                ],
                                className="me-2 d-flex align-items-center",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Span(
                                                "From",
                                                className="date-filter-label me-1 fw-bold",
                                            ),
                                            dbc.Input(
                                                id="date-from",
                                                type="date",
                                                value=default_start,
                                                size="sm",
                                                className="date-input-custom",
                                            ),
                                        ],
                                        className="d-flex align-items-center me-2",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "To",
                                                className="date-filter-label me-1 fw-bold",
                                            ),
                                            dbc.Input(
                                                id="date-to",
                                                type="date",
                                                value=default_end,
                                                size="sm",
                                                className="date-input-custom",
                                            ),
                                        ],
                                        className="d-flex align-items-center",
                                    ),
                                ],
                                className="d-flex align-items-center date-filter-group me-2",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button(
                                html.I(className="bi bi-moon-stars"),
                                id="theme-toggle",
                                color="link",
                                className="text-decoration-none fs-5 text-primary p-0",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dcc.Loading(
                                id="loading-refresh",
                                type="circle",
                                children=html.Div(
                                    id="refresh-status",
                                    className="text-success small fw-bold",
                                ),
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            html.Div(id="server-status-badge"),
                            width="auto",
                            className="me-1",
                        ),
                        dbc.Col(
                            dbc.Button(
                                html.I(className="bi bi-arrow-clockwise"),
                                id="refresh-btn",
                                color="primary",
                                size="sm",
                                title="Refresh",
                            ),
                            width="auto",
                        ),
                    ],
                    className="mb-2 align-items-center glass-panel p-2 rounded shadow-sm d-flex justify-content-between",
                    style={"position": "relative", "zIndex": 1050},
                ),

                # ── Server Status Offline Banner ─────────────────────
                html.Div(id="server-status-banner"),

                # ── KPI Cards (flex grid — all 7 in one row) ─────────
                html.Div(id="kpi-cards", className="kpi-grid mb-2"),

                dcc.Store(id="dashboard-trend-store"),
                html.Section([
                    dcc.Graph(id="approval-trend-chart", figure=_initial_fig,
                              config=_graph_cfg, responsive=True, className="report-comparison-chart"),
                    html.Div(id="approval-trend-status", className="small text-secondary px-3 pb-3",
                             **{"aria-live": "polite"}),
                ], className="glass-panel report-section mb-2"),

                # ── Stores & Intervals ───────────────────────────────
                dcc.Store(id="selected-users-store", data=available_users),
                dcc.Store(id="current-period-range", data={"start": default_start, "end": default_end}),
                dcc.Store(id="server-status-store", data=dm.get_server_status()),
                dcc.Interval(
                    id="auto-refresh-interval",
                    interval=5 * 60 * 1000,  # 5 minutes
                    n_intervals=0,
                ),
                dcc.Interval(
                    id="heartbeat-interval",
                    interval=30 * 1000,  # 30 seconds
                    n_intervals=0,
                ),

                # ── Chart row 1: Rework Ratio (50%) + Completed Duration (50%) ─────
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Graph(
                                id="rework-ratio-chart",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style=_graph_style,
                            ),
                            width=12,
                            lg=6,
                            className="mb-2 mb-lg-0",
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="individual-chart",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style=_graph_style,
                            ),
                            width=12,
                            lg=6,
                            className="mb-2 mb-lg-0",
                        ),
                    ],
                    className="mb-2",
                    style={"display": "flex", "alignItems": "stretch"},
                ),

                # ── Chart row 2: Pending reviews + Legend ───────────
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Graph(
                                id="pending-chart",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style=_graph_style,
                            ),
                            width=12,
                            lg=9,
                            className="mb-2 mb-lg-0",
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="universal-legend",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style=_graph_style,
                            ),
                            width=12,
                            lg=3,
                            className="mb-2 mb-lg-0",
                        ),
                    ],
                    className="mb-2",
                    style={"display": "flex", "alignItems": "stretch"},
                ),

                # ── Chart row 3: Assigned & Rework by ID (Dynamic Height) ──
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Graph(
                                id="assigned-chart",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style={"minHeight": "260px", "height": "auto"},
                            ),
                            width=12,
                            className="mb-2 mb-lg-0",
                        ),
                    ],
                    className="mb-2",
                ),

                # ── Tabs + Table ─────────────────────────────────────
            ],
            fluid=True,
            className="p-3",
        )
    ],
)

# ── Client-side theme toggle ─────────────────────────────────────────────
app.layout = add_reporting_shell(app.layout, today_iso())
app.layout = contain_graphs(app.layout)


@app.callback(
    Output("dashboard-page", "style"), Output("daily-report-page", "style"),
    Output("report-not-found", "style"), Output("nav-dashboard", "active"),
    Output("nav-daily-report", "active"), Output("dashboard-period-controls", "style"),
    Output("dashboard-date-controls", "style"),
    Output("user-report-page", "style"), Output("nav-user-report", "active"),
    Output('workflow-page', 'style'), Output('nav-workflow', 'active'),
    Output('settings-page', 'style'), Output('nav-settings', 'active'),
    Input("report-location", "pathname"),
)
def route_pages(pathname):
    dashboard = pathname in (None, "/", "/dashboard")
    daily = pathname == "/reports/daily"
    individual = pathname == "/reports/user"
    workflow = pathname == '/workflow'
    settings = pathname == '/settings'
    hidden = {"display": "none"}
    return (({}, hidden, hidden, True, False, {}, {}) if dashboard else (
        hidden, {} if daily else hidden, hidden if daily or individual or workflow or settings else {}, False, daily, hidden, hidden,
    )) + ({} if individual else hidden, individual, {} if workflow else hidden, workflow,
          {} if settings else hidden, settings)


@app.callback(Output('settings-settlement-panel', 'style'), Output('settings-mapping-panel', 'style'),
              Input('settings-tabs', 'active_tab'))
def show_settings_tab(tab):
    return ({}, {'display': 'none'}) if tab == 'settings-settlement' else ({'display': 'none'}, {})


@app.callback(
    Output('workflow-sync-store', 'data'),
    Input('report-location', 'pathname'), Input('refresh-btn', 'n_clicks'),
    Input('auto-refresh-interval', 'n_intervals'), Input('workflow-bell', 'n_clicks'),
    Input('workflow-notification-interval', 'n_intervals'),
    State('workflow-sync-store', 'data'),
)
def load_workflow_data(pathname, clicks, intervals, bell_clicks, notification_intervals=0, previous=None):
    if pathname not in (None, '/', '/dashboard', '/reports/daily', '/reports/user', '/workflow', '/settings'):
        raise dash.exceptions.PreventUpdate
    try:
        trigger = dash.ctx.triggered_id
    except dash.exceptions.MissingCallbackContextException:
        trigger = None
    try:
        return dm.get_workflow_data(force_refresh=trigger in ('refresh-btn', 'auto-refresh-interval', 'workflow-notification-interval', 'workflow-bell'))
    except Exception:
        # Source/store exceptions can include URLs/auth headers. Keep the UI
        # useful without exposing their raw bodies or forking into local storage.
        return {**(previous or {}), 'error': 'Workflow history could not be refreshed. Retry Refresh; configured storage has not been replaced.'}


@app.callback(Output('workflow-notification-modal', 'is_open'),
              Input('workflow-bell', 'n_clicks'), State('workflow-notification-modal', 'is_open'),
              prevent_initial_call=True)
def open_workflow_notifications(clicks, is_open):
    return not is_open


@app.callback(Output('workflow-notification-content', 'children'), Output('workflow-unread', 'children'),
              Output('workflow-mark-all-read', 'disabled'),
              Input('workflow-sync-store', 'data'), Input('workflow-read-change', 'data'))
def render_workflow_notifications(data, read_change):
    if read_change and read_change.get('error'):
        unread = (data or {}).get('inbox', {}).get('unread', 0)
        return dbc.Alert(read_change['error'], color='warning'), str(unread), not unread
    if read_change and data and not data.get('error'):
        # Re-read persistent state, rather than allowing an old browser Store
        # to undo later source updates or another viewer's read action.
        try:
            data = {**data, 'inbox': dm._workflow_history().notifications()}
        except Exception:
            return dbc.Alert('Shared inbox storage unavailable. Retry Refresh.', color='warning'), '—', True
    unread = (data or {}).get('inbox', {}).get('unread', 0)
    return notification_panel(data), str(unread), not unread


@app.callback(Output('workflow-toast-container', 'children'), Output('workflow-notice-seen', 'data'),
              Input('workflow-sync-store', 'data'), State('workflow-notice-seen', 'data'))
def show_new_workflow_notifications(data, seen):
    notices, seen = notification_updates(data, seen)
    return notification_toasts(notices), seen


@app.callback(Output('workflow-read-change', 'data', allow_duplicate=True),
              Input('workflow-mark-all-read', 'n_clicks'), prevent_initial_call=True)
def read_all_workflow_notifications(clicks):
    if not clicks:
        raise dash.exceptions.PreventUpdate
    try:
        dm.mark_all_workflow_notifications_read()
        from uuid import uuid4
        return {'all': True, 'read': True, 'change_id': uuid4().hex}
    except Exception:
        return {'error': 'Could not save read state. Retry Mark all read.'}


@app.callback(Output('report-location', 'href'), Output('workflow-read-change', 'data'),
              Output('workflow-notification-modal', 'is_open', allow_duplicate=True),
              Input({'type': 'workflow-notice', 'id': ALL}, 'n_clicks'),
              Input({'type': 'workflow-toast-notice', 'id': ALL}, 'n_clicks'), prevent_initial_call=True)
def read_workflow_notification(clicks, toast_clicks=None):
    trigger = dash.ctx.triggered_id
    if not isinstance(trigger, dict) or not any((clicks or []) + (toast_clicks or [])):
        raise dash.exceptions.PreventUpdate
    try:
        link, inbox = dm.mark_workflow_notification_read(trigger['id'])
    except Exception:
        # Keep the current location and show a retry instead of claiming read.
        return dash.no_update, {'error': 'Could not save read state. Retry this entry.'}, True
    return link, {'id': trigger['id'], 'read': True}, False


@app.callback(Output('workflow-batch', 'value'), Output('workflow-task', 'value'),
              Output('workflow-member', 'value'), Output('workflow-stage', 'value'),
              Output('workflow-evidence', 'value'), Output('workflow-action', 'value'),
              Output('workflow-search', 'value'), Output('workflow-from', 'value'), Output('workflow-to', 'value'),
              Input('report-location', 'search'))
def workflow_deep_link(search):
    from urllib.parse import parse_qs
    query = parse_qs((search or '').lstrip('?'))
    return (query.get('batch', [None])[0], query.get('task', [None])[0]) + (None,) * 7


@app.callback(Output('workflow-member', 'options'), Input('workflow-sync-store', 'data'))
def workflow_members(data):
    return [{'label': member, 'value': member} for member in (data or {}).get('members', [])]


@app.callback(Output('workflow-history-table', 'page_current'),
              Input('workflow-member', 'value'), Input('workflow-batch', 'value'), Input('workflow-task', 'value'),
              Input('workflow-stage', 'value'), Input('workflow-evidence', 'value'), Input('workflow-action', 'value'),
              Input('workflow-search', 'value'), Input('workflow-from', 'value'), Input('workflow-to', 'value'))
def reset_workflow_page(*filters):
    return 0


@app.callback(Output('workflow-history-table', 'data'), Output('workflow-history-table', 'columns'),
              Output('workflow-history-table', 'page_count'), Output('workflow-history-status', 'children'),
              Output('workflow-batch-state', 'children'), Output('workflow-history-table', 'style_cell'),
              Output('workflow-history-table', 'style_header'),
              Output('workflow-history-table', 'tooltip_data'),
              Input('report-location', 'pathname'), Input('workflow-sync-store', 'data'),
              Input('workflow-member', 'value'), Input('workflow-batch', 'value'), Input('workflow-task', 'value'),
              Input('workflow-stage', 'value'), Input('workflow-evidence', 'value'), Input('workflow-action', 'value'),
              Input('workflow-search', 'value'), Input('workflow-from', 'value'), Input('workflow-to', 'value'),
              Input('workflow-history-table', 'page_current'), Input('theme-toggle', 'n_clicks'))
def render_workflow_history(pathname, status, member, batch, task, stage, evidence, action, search, start, end, page, theme):
    from datetime import date
    if pathname != '/workflow':
        raise dash.exceptions.PreventUpdate
    dark = (theme or 0) % 2 == 0
    style = {'textAlign': 'left', 'padding': '10px', 'minWidth': '140px', 'maxWidth': '300px',
             'whiteSpace': 'normal', 'overflowWrap': 'anywhere',
             'backgroundColor': '#161b26' if dark else '#fff', 'color': '#e2e8f0' if dark else '#1e293b',
             'border': '1px solid #64748b'}
    header = {'fontWeight': 'bold', 'backgroundColor': '#252530' if dark else '#f1f5f9'}
    try:
        for value in (start, end):
            if value: date.fromisoformat(value)
        if start and end and start > end:
            raise ValueError('Date range')
    except ValueError:
        return [], [], 0, 'Choose a valid inclusive date range.', None, style, header, []
    if not status:
        return [], [], 0, 'Loading recorded history…', None, style, header, []
    filters = {'member': member, 'batch_id': (batch or '').strip(), 'task_id': (task or '').strip(),
               'stage': stage, 'provenance': evidence, 'action': action, 'search': search, 'start': start, 'end': end}
    try:
        data = dm.get_workflow_page(filters, (page or 0) + 1, 25)
        rows, columns = history_table(data)
    except Exception:
        return [], [], 0, 'Workflow storage unavailable. Retry Refresh.', None, style, header, []
    count = data['total']
    message = f"{count} matching actions · {data['storage']} · observed times and synthetic prerequisites are distinct."
    if status.get('error'):
        message += ' Refresh failed; showing persisted history.'
    elif any(row.get('error') or not row.get('complete') or row.get('limited') for row in status.get('checkpoints', [])):
        message += ' Source history is partial; further refreshes continue ingestion.'
    ledger = getattr(dm, '_batches_master_cache', {})
    current = ledger.get(batch) if isinstance(ledger, dict) and batch else None
    state = dbc.Alert(f"Latest batch state: {current.get('status')} · observed {current.get('last_updated') or 'time unavailable'}. "
                      'This current state is separate from historical action times.', color='info') if current else None
    tooltips = [{key: {'value': row.get(key) or '', 'type': 'text'} for key in ('Reason', 'Cycle')} for row in rows]
    return rows, columns, (count + 24) // 25, message, state, style, header, tooltips


@app.callback(
    Output("daily-report-date", "value"), Output("daily-date-follows-today", "data"),
    Output("daily-report-date", "max"),
    Input("daily-report-today", "n_clicks"), Input("auto-refresh-interval", "n_intervals"),
    Input("daily-report-date", "value"),
    State("daily-date-follows-today", "data"),
    prevent_initial_call=True,
)
def follow_daily_date(n_clicks, n_intervals, selected_date, follows_today):
    current = today_iso()
    trigger = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    if trigger == "daily-report-today":
        return current, True, current
    if trigger == "daily-report-date":
        return dash.no_update, selected_date == current, current
    return current if follows_today else dash.no_update, follows_today, current


@app.callback(
    Output("daily-report-store", "data"),
    Input("report-location", "pathname"), Input("refresh-btn", "n_clicks"),
    Input("auto-refresh-interval", "n_intervals"), Input("daily-report-date", "value"),
)
def load_daily_report(pathname, n_clicks, n_intervals, report_date):
    if pathname != "/reports/daily":
        raise dash.exceptions.PreventUpdate
    try:
        trigger = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    except (AttributeError, IndexError, dash.exceptions.MissingCallbackContextException):
        trigger = None
    # Reuse a recent capture on entry; explicit/timed refresh reads live. Pure
    # theme/legend interactions do not invoke this callback.
    try:
        return dm.get_daily_report_data(report_date=report_date or today_iso(),
            force_refresh=trigger in ("refresh-btn", "auto-refresh-interval"))
    except ValueError:
        return {"validation_error": "Choose a valid report date on or before today in India time."}


@app.callback(
    Output("daily-report-content", "children"),
    Output("refresh-status", "children", allow_duplicate=True),
    Output("theme-toggle", "children", allow_duplicate=True),
    Input("daily-report-store", "data"), Input("theme-toggle", "n_clicks"),
    Input("report-location", "pathname"),
    prevent_initial_call=True,
)
def render_daily_report(data, theme_clicks, pathname):
    if pathname != "/reports/daily":
        raise dash.exceptions.PreventUpdate
    is_dark = (theme_clicks or 0) % 2 == 0
    metadata = (data or {}).get("today", {}).get("metadata", {})
    status = "Loading report…" if not data else (
        "Daily data unavailable" if not metadata.get("available") else
        f"Saved: {metadata.get('captured_at', 'earlier capture')}" if metadata.get("is_snapshot") else
        f"Recorded: {metadata.get('captured_at', 'available daily history')}"
    )
    icon = html.I(className="bi bi-sun-fill text-warning fs-5" if is_dark else "bi bi-moon-stars-fill text-primary fs-5",
                  title="Switch to Light Theme" if is_dark else "Switch to Dark Theme")
    return build_daily_report_content(data, is_dark), status, icon


@app.callback(
    Output("user-report-date", "value"), Output("user-date-follows-today", "data"),
    Output("user-report-date", "max"),
    Input("user-report-today", "n_clicks"), Input("auto-refresh-interval", "n_intervals"),
    Input("user-report-date", "value"), State("user-date-follows-today", "data"),
    prevent_initial_call=True,
)
def follow_user_date(n_clicks, n_intervals, selected_date, follows_today):
    current = today_iso()
    trigger = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    if trigger == "user-report-today":
        return current, True, current
    if trigger == "user-report-date":
        return dash.no_update, selected_date == current, current
    return current if follows_today else dash.no_update, follows_today, current


@app.callback(
    Output("user-report-store", "data"),
    Input("report-location", "pathname"), Input("refresh-btn", "n_clicks"),
    Input("auto-refresh-interval", "n_intervals"), Input("user-report-date", "value"),
)
def load_user_report(pathname, n_clicks, n_intervals, report_date):
    if pathname != "/reports/user":
        raise dash.exceptions.PreventUpdate
    try:
        trigger = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    except (AttributeError, IndexError, dash.exceptions.MissingCallbackContextException):
        trigger = None
    try:
        return dm.get_user_report_data(report_date=report_date or today_iso(),
            force_refresh=trigger in ("refresh-btn", "auto-refresh-interval"))
    except ValueError:
        return {"validation_error": "Choose a valid report date on or before today in India time."}


@app.callback(
    Output("user-report-person", "options"), Output("user-report-person", "value"),
    Input("user-report-store", "data"), State("user-report-person", "value"),
)
def update_report_people(data, selected):
    if not data or data.get("validation_error"):
        return dash.no_update, dash.no_update
    users = data.get("users", [])
    return [{"label": user, "value": user} for user in users], (
        selected if selected in users else users[0] if users else None)


@app.callback(
    Output("user-report-content", "children"),
    Output("refresh-status", "children", allow_duplicate=True),
    Output("theme-toggle", "children", allow_duplicate=True),
    Input("user-report-store", "data"), Input("user-report-person", "value"),
    Input("theme-toggle", "n_clicks"), Input("report-location", "pathname"),
    prevent_initial_call=True,
)
def render_user_report(data, user, theme_clicks, pathname):
    if pathname != "/reports/user":
        raise dash.exceptions.PreventUpdate
    is_dark = (theme_clicks or 0) % 2 == 0
    metadata = (data or {}).get("today", {}).get("metadata", {})
    status = "Loading report…" if not data else (
        "Daily data unavailable" if not metadata.get("available") else
        f"Saved: {metadata.get('captured_at', 'earlier capture')}" if metadata.get("is_snapshot") else
        f"Recorded: {metadata.get('captured_at', 'available daily history')}"
    )
    icon = html.I(className="bi bi-sun-fill text-warning fs-5" if is_dark else "bi bi-moon-stars-fill text-primary fs-5")
    return build_user_report_content(data, user, is_dark), status, icon


app.clientside_callback(
    dash.ClientsideFunction(namespace="clientside", function_name="toggleTheme"),
    Output("main-container", "className"),
    Input("theme-toggle", "n_clicks"),
)


# ── Quick date-range buttons ─────────────────────────────────────────────
@app.callback(
    Output("settlement-dropdown", "options"), Output("settlement-dropdown", "value"),
    Input("auto-refresh-interval", "n_intervals"),
    State("settlement-dropdown", "value"),
)
def refresh_period_options(n_intervals, selected):
    current_periods = dm.get_available_periods()
    options = [{"label": p["label"], "value": p["value"]} for p in current_periods]
    if selected in {p["value"] for p in current_periods}:
        return options, dash.no_update
    current = next((p for p in current_periods if p.get("is_current")), current_periods[-1])
    return options, current["value"]


@app.callback(
    [Output("date-from", "value"), Output("date-to", "value"), Output("current-period-range", "data")],
    [
        Input("btn-today", "n_clicks"),
        Input("settlement-dropdown", "value"),
        Input("auto-refresh-interval", "n_intervals"),
    ],
    [State("date-from", "value"), State("date-to", "value"), State("current-period-range", "data")],
    prevent_initial_call=True,
)
def quick_filters(btn_today, dropdown_val, n_intervals, selected_start, selected_end, followed_range):
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if trigger_id == "btn-today":
        today_str = today_iso()
        return today_str, today_str, None
    
    if trigger_id == "settlement-dropdown" and dropdown_val:
        start, end = dropdown_val.split("|")
        is_current = end == "current"
        if is_current:
            end = today_iso()
        return start, end, {"start": start, "end": end} if is_current else None
    if trigger_id == "auto-refresh-interval" and dropdown_val:
        start, end = dropdown_val.split("|")
        if (end == "current" and followed_range
                and selected_start == followed_range["start"] == start
                and selected_end == followed_range["end"] and selected_end != today_iso()):
            return start, today_iso(), {"start": start, "end": today_iso()}
    raise dash.exceptions.PreventUpdate


def _build_status_badge(status: dict):
    if status.get('configuration_error'):
        return html.Span('Mappings unverified',
                         className='text-warning me-2 px-2 py-1 glass-panel rounded border border-warning',
                         title='Account mappings could not be refreshed. Attribution may be outdated.')
    is_live = status.get("is_live", True)
    using_snap = status.get("is_using_snapshot", False)
    if is_live and not using_snap:
        return html.Span(
            [
                html.Span(
                    style={
                        "display": "inline-block",
                        "width": "8px",
                        "height": "8px",
                        "borderRadius": "50%",
                        "backgroundColor": "#10B981",
                        "marginRight": "6px",
                        "boxShadow": "0 0 6px #10B981",
                    }
                ),
                html.Span("Live (5m sync)", style={"fontSize": "0.78rem", "fontWeight": "600", "color": "#10B981"}),
            ],
            className="d-flex align-items-center me-2 px-2 py-1 glass-panel rounded",
            title="API server is online. Auto-syncing every 5 minutes.",
        )
    else:
        return html.Span(
            [
                html.Span(
                    style={
                        "display": "inline-block",
                        "width": "8px",
                        "height": "8px",
                        "borderRadius": "50%",
                        "backgroundColor": "#F59E0B",
                        "marginRight": "6px",
                    }
                ),
                html.Span("Offline (Snapshot)", style={"fontSize": "0.78rem", "fontWeight": "600", "color": "#F59E0B"}),
            ],
            className="d-flex align-items-center me-2 px-2 py-1 glass-panel rounded border border-warning",
            title=f"Server unreachable. Displaying snapshot from {status.get('last_sync_time', 'earlier')}.",
        )


def _build_status_banner(status: dict):
    if status.get('configuration_error'):
        return html.Div([
            dbc.Alert('Account mappings could not be refreshed. Showing the last verified mappings; '
                      'person totals may be attributed incorrectly. Refresh to retry.',
                      color='warning', className='py-2 small'),
            _build_status_banner({**status, 'configuration_error': None}),
        ])
    is_live = status.get("is_live", True)
    using_snap = status.get("is_using_snapshot", False)
    if not is_live or using_snap:
        sync_time = status.get("last_sync_time", "earlier")
        return dbc.Alert(
            [
                html.Div(
                    [
                        html.I(className="bi bi-exclamation-triangle-fill text-warning me-2", style={"fontSize": "1.1rem"}),
                        html.Strong("Server Offline: ", className="text-warning me-1"),
                        html.Span(
                            f"Remote API server is currently unreachable. Displaying cached data snapshot from {sync_time}. Heartbeat is checking every 30s to auto-sync once restored."
                        ),
                    ],
                    className="d-flex align-items-center flex-grow-1",
                ),
                html.Div(
                    [
                        html.Span(
                            className="spinner-grow spinner-grow-sm text-warning me-1",
                            style={"width": "7px", "height": "7px"},
                        ),
                        html.Span("Heartbeat Active", style={"fontSize": "0.75rem"}),
                    ],
                    className="badge bg-dark text-warning border border-warning ms-2 d-flex align-items-center",
                ),
            ],
            color="warning",
            className="py-1 px-3 mb-2 d-flex align-items-center justify-content-between shadow-sm border-warning",
            style={"borderRadius": "6px", "fontSize": "0.82rem", "backgroundColor": "rgba(245, 158, 11, 0.12)"},
        )
    return None


# ── Main dashboard callback ──────────────────────────────────────────────
def _prepare_pending_sources(force_refresh):
    """Prefetch current assignments; review inventory has its own callback."""
    for status in ('slice_assigned', 'slice_rework'):
        try:
            dm._current_task_stats(status, force_refresh)
        except Exception:
            pass  # The detailed frame records the individual queue failure.


def _dashboard_sources(start_date, end_date, force_refresh, selected_users):
    """Overlap independent queues and batch reads while sharing return histories."""
    dm.refresh_shared_configuration(force=force_refresh)
    def attempt(read, fallback):
        try:
            return read()
        except Exception:
            return fallback
    def unavailable(message):
        frame = pd.DataFrame()
        frame.attrs['chart_error'] = message
        return frame
    raw = attempt(lambda: dm.fetch_dashboard_data(start_date, end_date, force_refresh), {'_source_error': 'Unavailable'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(dm._refresh_worker(_prepare_pending_sources), force_refresh)
        batches = pool.submit(
            dm._refresh_worker(dm.sync_batches_master), start_date, end_date, force_refresh=force_refresh,
        )
        kpis = attempt(lambda: dm.get_summary_kpis(start_date, end_date, selected_users=selected_users, force_refresh=force_refresh), {})
        breakdown = attempt(lambda: dm.get_user_breakdown_df(start_date, end_date, force_refresh), unavailable('Completion data unavailable. Refresh to retry.'))
        # Return histories and dated batch reads run alongside KPI comparisons.
        # Both the ratio and assigned rows must use the completed batch sync.
        attempt(batches.result, None)
        ratio = attempt(lambda: dm.get_batch_rework_ratio_df(start_date, end_date, force_refresh=force_refresh), unavailable('Batch history unavailable. Refresh to retry.'))
        attempt(pending.result, None)
        detailed = attempt(lambda: dm.get_detailed_pending_assigned_df(
            start_date="2020-01-01", end_date=today_iso(), force_refresh=force_refresh, overview_data=raw,
            include_pending=False,
        ), unavailable('Current assignments unavailable. Refresh to retry.'))
    return raw, kpis, breakdown, ratio, detailed


def _refresh_scope(callback):
    """Share source reads and persist once across the whole rendered response."""
    @wraps(callback)
    def scoped(*args, **kwargs):
        with dm.refresh_scope():
            return callback(*args, **kwargs)
    return scoped


@app.callback(
    [
        Output("kpi-cards", "children"),
        Output("rework-ratio-chart", "figure"),
        Output("individual-chart", "figure"),
        Output("assigned-chart", "figure"),
        Output("refresh-status", "children"),
        Output("theme-toggle", "children"),
        Output("selected-users-store", "data"),
        Output("server-status-banner", "children"),
        Output("server-status-badge", "children"),
    ],
    [
        Input("refresh-btn", "n_clicks"),
        Input("auto-refresh-interval", "n_intervals"),
        Input("date-from", "value"),
        Input("date-to", "value"),
        Input("universal-legend", "restyleData"),
        Input("theme-toggle", "n_clicks"),
        Input("individual-chart", "clickData"),
        Input("report-location", "pathname"),
    ],
    [State("selected-users-store", "data")],
    running=[(Output("refresh-btn", "disabled"), True, False),
             (Output("auto-refresh-interval", "disabled"), True, False)],
)
def _dispatch_dashboard(n_clicks, n_intervals, start_date, end_date, restyle_data,
                        theme_clicks, ind_click, pathname, stored_users):
    # Dash passes Inputs before State; the handler keeps selection before route.
    return update_dashboard(n_clicks, n_intervals, start_date, end_date, restyle_data,
                            theme_clicks, None, ind_click, stored_users, pathname)


@app.callback(
    Output('pending-chart', 'figure'),
    Input('refresh-btn', 'n_clicks'), Input('report-location', 'pathname'),
    Input('workflow-sync-store', 'data'), Input('selected-users-store', 'data'),
    Input('theme-toggle', 'n_clicks'),
)
def update_pending_review(clicks, pathname, workflow, selected_users, theme_clicks):
    if pathname not in (None, '/', '/dashboard'):
        return dash.no_update
    trigger = dash.ctx.triggered_id
    # Workflow polling observes upstream mutations; there are no local review actions.
    force = trigger in (None, 'refresh-btn', 'report-location', 'workflow-sync-store')
    if trigger == 'workflow-sync-store':
        dm.invalidate_pending_review()
    dark = (theme_clicks or 0) % 2 == 0
    try:
        frame = dm.get_pending_review_df(force_refresh=force)
        if selected_users and set(selected_users) != set(available_users):
            frame = frame[frame['User'].isin(selected_users)]
        return build_pending_chart(frame, dark)
    except Exception:
        return state_figure('Pending review unavailable. Refresh to retry.', dark)


@_refresh_scope
def update_dashboard(
    n_clicks,
    n_intervals,
    start_date,
    end_date,
    restyle_data,
    theme_clicks,
    active_tab,
    ind_click,
    stored_users,
    pathname="/",
):
    if pathname not in (None, "/", "/dashboard"):
        return tuple([dash.no_update] * 9)
    # ── Theme ────────────────────────────────────────────────────────
    is_dark = True if theme_clicks is None else theme_clicks % 2 == 0
    toggle_label = (
        html.I(className="bi bi-sun-fill text-warning fs-5", title="Switch to Light Theme")
        if is_dark
        else html.I(className="bi bi-moon-stars-fill text-primary fs-5", title="Switch to Dark Theme")
    )

    if not start_date or not end_date:
        raise dash.exceptions.PreventUpdate

    # ── Determine trigger ────────────────────────────────────────────
    ctx = dash.callback_context
    try:
        triggered_id = (
            ctx.triggered[0]["prop_id"].split(".")[0]
            if ctx and ctx.triggered
            else None
        )
    except Exception:
        triggered_id = None

    force_refresh = triggered_id in ["refresh-btn", "auto-refresh-interval"]
    # Retry real data requests on each refresh even if the last heartbeat failed.
    # The data manager retains the matching cached data if recovery fails.

    # ── User selection / filtering ───────────────────────────────────
    current_selection = (
        list(stored_users)
        if stored_users is not None
        else list(available_users)
    )

    if triggered_id == "universal-legend" and restyle_data:
        updates = restyle_data[0]
        indices = restyle_data[1]
        if "visible" in updates:
            for i, idx in enumerate(indices):
                vis = (
                    updates["visible"][i]
                    if isinstance(updates["visible"], list)
                    else updates["visible"]
                )
                if idx < len(available_users):
                    user = available_users[idx]
                    if vis == "legendonly" and user in current_selection:
                        current_selection.remove(user)
                    elif (
                        vis is True or vis is None
                    ) and user not in current_selection:
                        current_selection.append(user)

    if triggered_id == "individual-chart":
        click_data = ind_click
        if click_data and "points" in click_data:
            pt = click_data["points"][0]
            clicked_user = pt.get("x") or pt.get("label")
            if clicked_user:
                if clicked_user in current_selection:
                    current_selection.remove(clicked_user)
                else:
                    current_selection.append(clicked_user)

    effective_users = current_selection if current_selection else None
    is_filtering = (
        effective_users is not None
        and len(effective_users) < len(available_users)
    )

    # ── Fetch data ───────────────────────────────────────────────────
    raw_data, kpis, breakdown_df, ratio_df, detailed_df = _dashboard_sources(
        start_date, end_date, force_refresh, effective_users if is_filtering else None,
    )
    breakdowns = raw_data.get("breakdowns", {})
    funnel_list = breakdowns.get("slice_funnel", [])
    funnel_map = {f.get("key"): f for f in funnel_list}

    # ── KPI cards ────────────────────────────────────────────────────
    try:
        kpi_layout = build_kpi_layout(kpis, funnel_map, is_dark)
        if raw_data.get('_source_error') or kpis.get('_source_error'):
            kpi_layout = [dbc.Alert('Summary data unavailable. Refresh to retry.', color='warning')]
    except Exception:
        kpi_layout = [dbc.Alert('Summary data unavailable. Refresh to retry.', color='warning')]

    # ── Legend ───────────────────────────────────────────────────────
    selection_changed = triggered_id in ("universal-legend", "individual-chart")

    # ── Breakdown data ───────────────────────────────────────────────
    if is_filtering and effective_users and 'User' in breakdown_df:
        breakdown_df = breakdown_df[
            breakdown_df["User"].isin(effective_users)
        ]

    # ── Rework Ratio chart ───────────────────────────────────────────
    fig_rework = build_rework_ratio_chart(
        ratio_df, start_date, is_dark, effective_users, is_filtering
    )

    # ── Individual chart ─────────────────────────────────────────────
    fig_ind = build_individual_chart(
        breakdown_df, start_date, end_date, is_dark
    )

    # ── Pending + Assigned charts ────────────────────────────────────
    if is_filtering and effective_users and not detailed_df.empty:
        detailed_df = detailed_df[
            (detailed_df["User"].isin(effective_users))
            | (detailed_df["User"] == "Assignable Pool")
        ]

    assigned_df = (
        detailed_df[
            detailed_df["Stage"].isin(["New Assigned", "Rework Assigned"])
        ]
        if not detailed_df.empty
        else detailed_df
    )
    fig_assigned = build_assigned_chart(assigned_df, is_dark)

    # ── Tab content ──────────────────────────────────────────────────

    srv_status = dm.get_server_status()
    badge_el = _build_status_badge(srv_status)
    banner_el = _build_status_banner(srv_status)

    if srv_status.get("is_using_snapshot", False) or not srv_status.get("is_live", True):
        status_msg = f"Snapshot: {srv_status.get('last_sync_time', 'Cached')} (Offline)"
    else:
        status_msg = f"Updated: {datetime.now().strftime('%H:%M:%S')} (Live)"

    return (
        kpi_layout,
        fig_rework,
        fig_ind,
        fig_assigned,
        status_msg,
        toggle_label if triggered_id in (None, "theme-toggle") else dash.no_update,
        current_selection if selection_changed else dash.no_update,
        banner_el,
        badge_el,
    )


# ── Heartbeat polling callback ───────────────────────────────────────────
@app.callback(
    [
        Output("server-status-store", "data"),
        Output("server-status-badge", "children", allow_duplicate=True),
        Output("server-status-banner", "children", allow_duplicate=True),
    ],
    [Input("heartbeat-interval", "n_intervals")],
    prevent_initial_call=True,
)
def heartbeat_check(n_intervals):
    dm.check_server_heartbeat(timeout=2.5)
    status = dm.get_server_status()
    badge = _build_status_badge(status)
    banner = _build_status_banner(status)
    return status, badge, banner


@app.callback(
    Output("universal-legend", "figure"),
    Input("selected-users-store", "data"), Input("theme-toggle", "n_clicks"),
    Input("report-location", "pathname"),
)
def render_dashboard_legend(selected_users, theme_clicks, pathname):
    """Paint the user controls immediately, independently of upstream latency."""
    if pathname not in (None, "/", "/dashboard"):
        raise dash.exceptions.PreventUpdate
    return build_legend_figure(available_users,
        available_users if selected_users is None else selected_users,
        theme_clicks is None or theme_clicks % 2 == 0)


@app.callback(
    Output("dashboard-trend-store", "data"),
    Input("date-to", "value"), Input("refresh-btn", "n_clicks"),
    Input("auto-refresh-interval", "n_intervals"), Input("report-location", "pathname"),
)
def load_dashboard_trend(end_date, n_clicks, n_intervals, pathname):
    """Load history independently, so aggregate/queue latency cannot block it."""
    if pathname not in (None, "/", "/dashboard") or not end_date:
        raise dash.exceptions.PreventUpdate
    trigger = dash.ctx.triggered_id
    return dm.get_daily_report_data(min(end_date, today_iso()),
        force_refresh=trigger in ("refresh-btn", "auto-refresh-interval"))


@app.callback(
    Output("approval-trend-chart", "figure"), Output("approval-trend-status", "children"),
    Input("dashboard-trend-store", "data"), Input("selected-users-store", "data"),
    Input("theme-toggle", "n_clicks"), Input("report-location", "pathname"),
    Input('workflow-sync-store', 'data'),
)
def render_dashboard_trend(data, selected_users, theme_clicks, pathname, workflow=None):
    if pathname not in (None, "/", "/dashboard"):
        raise dash.exceptions.PreventUpdate
    if not data:
        return empty_fig(), "Loading recorded work history…"
    # The dashboard's initial all-users selection must include new accounts.
    users = selected_users if selected_users and len(selected_users) < len(available_users) else None
    report = prepare_approval_trend(data, users, workflow.get('approvals') if workflow else None)
    is_dark = theme_clicks is None or theme_clicks % 2 == 0
    note = (f"{report['recorded_days']} of {report['calendar_days']} days recorded · Asia/Kolkata. "
            "Missing dates appear as gaps. " + report['approval_status'])
    if report['range_end'] == today_iso():
        note += " Today is provisional."
    if report['captured_at']:
        note += f" Latest capture: {report['captured_at']}."
    if report['stale']:
        note += " Saved evidence is shown; refresh or persistence could not be verified."
    if workflow and not workflow.get('error'):
        note += (f" Workflow history: {workflow.get('events', 0)} actions, "
                 f"{workflow.get('synthetic', 0)} synthetic prerequisites. Unknown approval times are excluded from daily hours.")
    return build_approval_trend_chart(report, is_dark), note



register_management_callbacks(app, dm)

def run_local(port=8050):
    # Werkzeug's Windows process reloader can close the serving thread's socket.
    # Keep debug tools available and restart manually after Python edits on Windows.
    reload_code = sys.platform != 'win32'
    app.run(debug=True, port=port, use_reloader=reload_code,
            dev_tools_hot_reload=reload_code)


if __name__ == "__main__":
    run_local()
