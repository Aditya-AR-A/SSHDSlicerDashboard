"""
Slicing Performance & Settlement Dashboard — Main Application.

Layout and callbacks. All chart/table builders live in the plots/ package.
"""

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from dash import Input, Output, State, dcc, html
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
    build_error_rework_chart,
    build_legend_figure,
    build_kpi_layout,
    create_table,
)
from slicing_dashboard.plots.theme import CHART_HEIGHT
from slicing_dashboard.management.ui import layout_settlement_management, layout_user_mapping, register_management_callbacks
from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.processing.daily_work import EXCLUDED
from slicing_dashboard.pages.components import add_reporting_shell
from slicing_dashboard.pages.daily_report import build_daily_report_content
from slicing_dashboard.reporting.dashboard_reports import prepare_daily_table, prepare_daily_work_chart_rows

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

def _available_user_names(snapshot, mappings):
    names = list(snapshot.get("available_users", [])) + list(mappings.values())
    return sorted({name for name in names if name and name not in EXCLUDED})


available_users = _available_user_names(dm._snapshot_payload, dm.user_mapping)
if not available_users:
    available_users = ["Aditya", "Deepak", "Komal", "Pawan", "Priya", "Rajni", "Riya", "Sanddep"]

# ── Chart style shorthand ────────────────────────────────────────────────
_graph_style = {"height": f"{CHART_HEIGHT}px"}
_graph_cfg = {"displayModeBar": False}
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

                # ── Chart row 2: Pending + New Work + Legend ─────────
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
                            lg=5,
                            className="mb-2 mb-lg-0",
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="error-rework-chart",
                                figure=_initial_fig,
                                config=_graph_cfg,
                                className="glass-panel p-1 rounded shadow-sm chart-compact",
                                style=_graph_style,
                            ),
                            width=12,
                            lg=4,
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
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Tabs(
                                    [
                                        dbc.Tab(
                                            label="Today's Work",
                                            tab_id="tab-today",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                        dbc.Tab(
                                            label="Yesterday's Work",
                                            tab_id="tab-yesterday",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                        dbc.Tab(
                                            label="Settlement Overview",
                                            tab_id="tab-settlement",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                        dbc.Tab(
                                            label="Slice Data Overview",
                                            tab_id="tab-overview",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                        dbc.Tab(
                                            label="Settlement Management",
                                            tab_id="tab-manage-settlement",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                        dbc.Tab(
                                            label="User Mapping",
                                            tab_id="tab-manage-mapping",
                                            label_class_name="d-flex align-items-center gap-2",
                                        ),
                                    ],
                                    id="tabs",
                                    active_tab="tab-today",
                                    className="mb-1",
                                ),
                                html.Div(
                                    id="tabs-content",
                                    className="glass-panel p-2 rounded shadow-sm",
                                ),
                            ]
                        )
                    ]
                ),
            ],
            fluid=True,
            className="p-3",
        )
    ],
)

# ── Client-side theme toggle ─────────────────────────────────────────────
app.layout = add_reporting_shell(app.layout, today_iso())


@app.callback(
    Output("dashboard-page", "style"), Output("daily-report-page", "style"),
    Output("report-not-found", "style"), Output("nav-dashboard", "active"),
    Output("nav-daily-report", "active"), Output("dashboard-period-controls", "style"),
    Output("dashboard-date-controls", "style"),
    Input("report-location", "pathname"),
)
def route_pages(pathname):
    dashboard = pathname in (None, "/", "/dashboard")
    daily = pathname == "/reports/daily"
    hidden = {"display": "none"}
    return ({}, hidden, hidden, True, False, {}, {}) if dashboard else (
        hidden, {} if daily else hidden, hidden if daily else {}, False, daily, hidden, hidden,
    )


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
    # Refresh the live capture on entry or an explicit/timed refresh. Pure
    # theme/legend interactions do not invoke this callback.
    try:
        return dm.get_daily_report_data(report_date=report_date or today_iso(),
            force_refresh=trigger in (None, "report-location", "refresh-btn", "auto-refresh-interval"))
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
    """Fetch queues concurrently; build assigned rows after the batch sync."""
    dm.fetch_annotator_efficiency(
        "2020-01-01", today_iso(), role=2, force_refresh=force_refresh, include_summary=False,
    )


def _dashboard_sources(start_date, end_date, force_refresh, selected_users):
    """Overlap independent queues and inventory while sharing return histories."""
    raw = dm.fetch_dashboard_data(start_date, end_date, force_refresh)
    daily_date = start_date if start_date == end_date else today_iso()
    with ThreadPoolExecutor(max_workers=3) as pool:
        inventory = pool.submit(dm._refresh_worker(dm.prepare_daily_work), daily_date) if force_refresh else None
        pending = pool.submit(dm._refresh_worker(_prepare_pending_sources), force_refresh)
        batches = pool.submit(
            dm._refresh_worker(dm.sync_batches_master), start_date, end_date, force_refresh=force_refresh,
        )
        kpis = dm.get_summary_kpis(start_date, end_date, selected_users=selected_users, force_refresh=force_refresh)
        breakdown = dm.get_user_breakdown_df(start_date, end_date, force_refresh)
        # Return histories and dated batch reads run alongside KPI comparisons.
        # Both the ratio and assigned rows must use the completed batch sync.
        batches.result()
        ratio = dm.get_batch_rework_ratio_df(start_date, end_date, force_refresh=force_refresh)
        if inventory is not None:
            try:
                inventory.result()
            except Exception:
                # The daily getter retains its existing retry and dated fallback.
                pass
        pending.result()
        detailed = dm.get_detailed_pending_assigned_df(
            start_date="2020-01-01", end_date=today_iso(), force_refresh=force_refresh, overview_data=raw,
        )
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
        Output("universal-legend", "figure"),
        Output("rework-ratio-chart", "figure"),
        Output("error-rework-chart", "figure"),
        Output("individual-chart", "figure"),
        Output("pending-chart", "figure"),
        Output("assigned-chart", "figure"),
        Output("tabs-content", "children"),
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
        Input("tabs", "active_tab"),
        Input("individual-chart", "clickData"),
        Input("error-rework-chart", "clickData"),
        Input("report-location", "pathname"),
    ],
    [State("selected-users-store", "data")],
    running=[(Output("refresh-btn", "disabled"), True, False),
             (Output("auto-refresh-interval", "disabled"), True, False)],
)
def _dispatch_dashboard(n_clicks, n_intervals, start_date, end_date, restyle_data,
                        theme_clicks, active_tab, ind_click, err_click, pathname, stored_users):
    # Dash passes Inputs before State. Keep the existing handler's positional
    # API intact for callers while adding the route as a reactive Input.
    return update_dashboard(n_clicks, n_intervals, start_date, end_date, restyle_data,
                            theme_clicks, active_tab, ind_click, err_click, stored_users, pathname)


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
    err_click,
    stored_users,
    pathname="/",
):
    if pathname not in (None, "/", "/dashboard"):
        return tuple([dash.no_update] * 13)
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

    if triggered_id in ["individual-chart", "error-rework-chart"]:
        click_data = (
            ind_click if triggered_id == "individual-chart" else err_click
        )
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

    if triggered_id == "tabs":
        # Charts are independent of the selected table or management form.
        outputs = [dash.no_update] * 13
        outputs[7] = _render_tab(
            active_tab, is_dark, is_filtering, effective_users, end_date, False,
        )
        return tuple(outputs)

    # ── Fetch data ───────────────────────────────────────────────────
    raw_data, kpis, breakdown_df, ratio_df, detailed_df = _dashboard_sources(
        start_date, end_date, force_refresh, effective_users if is_filtering else None,
    )
    breakdowns = raw_data.get("breakdowns", {})
    funnel_list = breakdowns.get("slice_funnel", [])
    funnel_map = {f.get("key"): f for f in funnel_list}

    # ── KPI cards ────────────────────────────────────────────────────
    kpi_layout = build_kpi_layout(kpis, funnel_map, is_dark)

    # ── Legend ───────────────────────────────────────────────────────
    selection_changed = triggered_id in (None, "universal-legend", "individual-chart", "error-rework-chart")
    fig_legend = build_legend_figure(available_users, current_selection, is_dark) if (
        selection_changed or triggered_id == "theme-toggle"
    ) else dash.no_update

    # ── Breakdown data ───────────────────────────────────────────────
    if is_filtering and effective_users:
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

    # ── Error / Rework chart ─────────────────────────────────────────
    fig_err = _build_work_chart(
        breakdown_df,
        start_date,
        end_date,
        is_dark,
        effective_users if is_filtering else None,
        force_refresh,
    )

    # ── Pending + Assigned charts ────────────────────────────────────
    if is_filtering and effective_users and not detailed_df.empty:
        detailed_df = detailed_df[
            (detailed_df["User"].isin(effective_users))
            | (detailed_df["User"] == "Assignable Pool")
        ]

    pending_df = (
        detailed_df[
            detailed_df["Stage"].isin(
                ["Pending Leader", "Pending Auditor", "Pending Admin"]
            )
        ]
        if not detailed_df.empty
        else pd.DataFrame()
    )
    fig_pending = build_pending_chart(pending_df, is_dark)

    assigned_df = (
        detailed_df[
            detailed_df["Stage"].isin(["New Assigned", "Rework Assigned"])
        ]
        if not detailed_df.empty
        else pd.DataFrame()
    )
    fig_assigned = build_assigned_chart(assigned_df, is_dark)

    # ── Tab content ──────────────────────────────────────────────────
    tab_content = dash.no_update if (
        active_tab in ["tab-manage-settlement", "tab-manage-mapping"]
        and triggered_id != "tabs"
        and triggered_id is not None
    ) else _render_tab(
        active_tab,
        is_dark,
        is_filtering,
        effective_users,
        end_date,
        force_refresh,
        daily_work_date=start_date if start_date == end_date else today_iso(),
    )

    srv_status = dm.get_server_status()
    badge_el = _build_status_badge(srv_status)
    banner_el = _build_status_banner(srv_status)

    if srv_status.get("is_using_snapshot", False) or not srv_status.get("is_live", True):
        status_msg = f"Snapshot: {srv_status.get('last_sync_time', 'Cached')} (Offline)"
    else:
        status_msg = f"Updated: {datetime.now().strftime('%H:%M:%S')} (Live)"

    return (
        kpi_layout,
        fig_legend,
        fig_rework,
        fig_err,
        fig_ind,
        fig_pending,
        fig_assigned,
        tab_content,
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


# ── Helper: build the New Work + Rework chart ────────────────────────────
def _build_work_chart(
    breakdown_df,
    start_date,
    end_date,
    is_dark,
    effective_users,
    force_refresh,
):
    """Daily chart uses the same verified submission rows as the daily table."""
    target_date = start_date if start_date == end_date else today_iso()
    label = "Today" if target_date == today_iso() else target_date
    daily = dm.get_todays_work_df(target_date, force_refresh=force_refresh)
    if daily.empty:
        fig = build_error_rework_chart(pd.DataFrame(), label, is_dark)
        if daily.attrs.get('error'):
            fig.layout.annotations[0].text = "Daily submissions could not be verified. Retry Refresh."
        return fig
    if effective_users:
        daily = daily[daily['User'].isin(effective_users)]
    work = prepare_daily_work_chart_rows({"date": target_date, "rows": daily.to_dict("records"),
                                         "metadata": dict(daily.attrs, available=not bool(daily.attrs.get('error')) or not daily.empty)})
    figure = build_error_rework_chart(work, label, is_dark)
    if daily.attrs.get('is_snapshot'):
        figure.update_layout(title=f"{label} · Submitted Video Duration (cached)")
    return figure


# ── Helper: render tab content ───────────────────────────────────────────
def _render_tab(
    active_tab,
    is_dark,
    is_filtering,
    effective_users,
    end_date,
    force_refresh,
    daily_work_date=None,
):
    """Render the active tab's table content."""
    if active_tab in ("tab-today", "tab-yesterday"):
        target_date = today_iso() if active_tab == "tab-today" else (
            datetime.strptime(today_iso(), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        raw = dm.get_todays_work_df(target_date, force_refresh=force_refresh and target_date != daily_work_date)
        if is_filtering and effective_users and not raw.empty:
            raw = raw[raw['User'].isin(effective_users)].copy()
        if raw.empty and raw.attrs.get('error'):
            return dbc.Alert("Daily submissions could not be verified. Please retry Refresh.", color="warning")
        if raw.empty:
            return html.Div("No submissions recorded for this day.")
        daily = pd.DataFrame(prepare_daily_table({"date": target_date, "rows": raw.to_dict("records"),
                                                  "metadata": dict(raw.attrs, available=True)}))
        note = "Observed unique task submissions in India time. Video duration only; approvals and pending rework are excluded."
        if raw.attrs.get('captured_at'):
            note += f" Verified scan: {raw.attrs['captured_at']}."
        if raw.attrs.get('is_snapshot'):
            note = f"Cached verified submissions from {raw.attrs.get('captured_at', 'an earlier refresh')}. Refresh failed; figures may be stale."
        return html.Div([html.P(note, className='text-secondary small'), create_table(daily, is_dark)])

    elif active_tab == "tab-overview":
        overview_df = dm.get_slice_data_overview_df(
            "2026-09-01",
            end_date,
            use_raw_names=True,
            force_refresh=force_refresh,
        )
        if is_filtering and effective_users and not overview_df.empty:
            filtered_records = []
            for _, row in overview_df.iterrows():
                raw_user = row["Username"]
                if raw_user == "All Slicers":
                    filtered_records.append(row)
                    continue
                uid_str = (
                    raw_user.replace("user-", "")
                    if raw_user.startswith("user-")
                    else ""
                )
                uid = int(uid_str) if uid_str.isdigit() else 0
                canonical = dm._get_canonical_name(uid, raw_user)
                if canonical in effective_users:
                    filtered_records.append(row)
            overview_df = (
                pd.DataFrame(filtered_records)
                if filtered_records
                else pd.DataFrame(columns=overview_df.columns)
            )
        return create_table(overview_df, is_dark)
    elif active_tab == "tab-manage-settlement":
        return layout_settlement_management(is_dark=is_dark)
        
    elif active_tab == "tab-manage-mapping":
        return layout_user_mapping(is_dark=is_dark)

    else:
        # Settlement tab
        full_breakdown_df = dm.get_user_breakdown_df(
            "2026-09-01", end_date, force_refresh
        )
        if is_filtering and effective_users:
            full_breakdown_df = full_breakdown_df[
                full_breakdown_df["User"].isin(effective_users)
            ]
        settlement_df = dm.get_settlement_df(full_breakdown_df)
        if not settlement_df.empty:

            def format_settlement_val(val):
                if pd.isna(val) or val is None or val == 0:
                    return "00:00 (0.00h)"
                val = float(val)
                sec = int(round(val))
                h = sec // 3600
                m = (sec % 3600) // 60
                hours_dec = val / 3600.0
                return f"{h:02d}:{m:02d} ({hours_dec:.2f}h)"

            user_part = settlement_df[
                settlement_df["User"] != "TOTAL"
            ].sort_values(by="Remaining Payable", ascending=False)
            total_part = settlement_df[settlement_df["User"] == "TOTAL"]
            settlement_df = pd.concat(
                [user_part, total_part], ignore_index=True
            )

            duration_cols = [
                "Jul 1 - Aug 7 (Paid)",
                "Aug 8 - Aug 31 (Paid)",
                "Total Settled (Aug 31)",
                "Current Work (Unsettled)",
                "Remaining Payable",
            ]
            for col in duration_cols:
                if col in settlement_df.columns:
                    settlement_df[col] = settlement_df[col].apply(
                        format_settlement_val
                    )
        return create_table(settlement_df, is_dark)

register_management_callbacks(app, dm)

if __name__ == "__main__":
    app.run(debug=True, port=8050)
