"""
Slicing Performance & Settlement Dashboard — Main Application.

Layout and callbacks. All chart/table builders live in the plots/ package.
"""

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, dcc, html
from datetime import datetime, timedelta

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

# ── Bootstrap / initialise ───────────────────────────────────────────────
dm = DataManager()
periods = dm.get_available_periods()
default_period = next((p for p in periods if p.get('is_current')), periods[-1] if periods else {'start_date': '2026-09-01', 'end_date': datetime.now().strftime("%Y-%m-%d")})
default_start = default_period['start_date']
default_end = default_period['end_date']
default_period_value = f"{default_start}|{default_end}"


app = dash.Dash(
    __name__,
    title="SSHD Slicing Dashboard",
    update_title=None,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
)

try:
    all_users_df = dm.get_user_breakdown_df("2026-07-01", default_end)
    available_users = (
        sorted(all_users_df["User"].unique().tolist())
        if not all_users_df.empty
        else []
    )
except Exception:
    available_users = []

if not available_users and dm._snapshot_payload.get("available_users"):
    available_users = dm._snapshot_payload["available_users"]
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
app.clientside_callback(
    dash.ClientsideFunction(namespace="clientside", function_name="toggleTheme"),
    Output("main-container", "className"),
    Input("theme-toggle", "n_clicks"),
)


# ── Quick date-range buttons ─────────────────────────────────────────────
@app.callback(
    [Output("date-from", "value"), Output("date-to", "value")],
    [
        Input("btn-today", "n_clicks"),
        Input("settlement-dropdown", "value"),
    ],
    prevent_initial_call=True,
)
def quick_filters(btn_today, dropdown_val):
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if trigger_id == "btn-today":
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        return today_str, today_str
    
    if trigger_id == "settlement-dropdown" and dropdown_val:
        start, end = dropdown_val.split("|")
        return start, end

    return default_start, default_end


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
    ],
    [State("selected-users-store", "data")],
)
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
):
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

    # ── Fetch data ───────────────────────────────────────────────────
    raw_data = dm.fetch_dashboard_data(start_date, end_date, force_refresh)
    kpis = dm.get_summary_kpis(
        start_date,
        end_date,
        selected_users=effective_users if is_filtering else None,
        force_refresh=force_refresh,
    )
    breakdowns = raw_data.get("breakdowns", {})
    funnel_list = breakdowns.get("slice_funnel", [])
    funnel_map = {f.get("key"): f for f in funnel_list}

    # ── KPI cards ────────────────────────────────────────────────────
    kpi_layout = build_kpi_layout(kpis, funnel_map, is_dark)

    # ── Legend ───────────────────────────────────────────────────────
    fig_legend = build_legend_figure(
        available_users, current_selection, is_dark
    )

    # ── Breakdown data ───────────────────────────────────────────────
    breakdown_df = dm.get_user_breakdown_df(
        start_date, end_date, force_refresh
    )
    if is_filtering and effective_users:
        breakdown_df = breakdown_df[
            breakdown_df["User"].isin(effective_users)
        ]

    # ── Rework Ratio chart ───────────────────────────────────────────
    ratio_df = dm.get_batch_rework_ratio_df(start_date, end_date, force_refresh=force_refresh)
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
        effective_users,
        force_refresh,
    )

    # ── Pending + Assigned charts ────────────────────────────────────
    detailed_df = dm.get_detailed_pending_assigned_df(
        start_date="2020-01-01",
        end_date=datetime.now().strftime("%Y-%m-%d"),
        force_refresh=force_refresh,
    )
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
    tab_content = _render_tab(
        active_tab,
        is_dark,
        is_filtering,
        effective_users,
        end_date,
        force_refresh,
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
        toggle_label,
        current_selection,
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
    """Fetch work classification data and build the chart."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if start_date == end_date:
        target_work_date = start_date
        if start_date == today_str:
            chart_date_label = "Today"
        else:
            d = datetime.strptime(start_date, "%Y-%m-%d")
            chart_date_label = f"{d.strftime('%b')} {d.day}"
        try:
            t_df = dm.get_todays_work_df(
                target_work_date,
                force_refresh=force_refresh,
            )
            if not t_df.empty:
                work_df = pd.DataFrame({
                    "User": t_df["User"],
                    "New Work Duration": t_df["New Videos (First Time)"],
                    "Rework Duration": t_df.get("Reworks", 0.0),
                    "Total Work Duration": t_df["Total Duration"],
                    "RawID": t_df.get("RawID", ""),
                })
            else:
                work_df = breakdown_df.copy()
        except Exception:
            work_df = breakdown_df.copy()
    else:
        chart_date_label = "Selected Period"
        if not breakdown_df.empty and "New Work Duration" in breakdown_df.columns:
            work_df = pd.DataFrame({
                "User": breakdown_df["User"],
                "New Work Duration": breakdown_df["New Work Duration"],
                "Rework Duration": breakdown_df.get("Rework Duration", 0.0),
                "Total Work Duration": breakdown_df.get("Total Duration", 0.0),
                "RawID": "",
            })
        else:
            try:
                t_df = dm.get_todays_work_df(
                    today_str,
                    force_refresh=force_refresh,
                )
                if not t_df.empty:
                    chart_date_label = "Today"
                    work_df = pd.DataFrame({
                        "User": t_df["User"],
                        "New Work Duration": t_df["New Videos (First Time)"],
                        "Rework Duration": t_df.get("Reworks", 0.0),
                        "Total Work Duration": t_df["Total Duration"],
                        "RawID": t_df.get("RawID", ""),
                    })
            except Exception:
                work_df = pd.DataFrame()

    if not work_df.empty:
        if "New Work Duration" not in work_df.columns:
            if "Submitted Duration" in work_df.columns:
                total_worked = work_df[
                    ["Completed Duration", "Submitted Duration"]
                ].max(axis=1)
            else:
                total_worked = work_df.get("Completed Duration", 0)
            work_df["New Work Duration"] = (
                total_worked - work_df.get("Rework Duration", 0)
            ).clip(lower=0)

        if "Rework Duration" not in work_df.columns:
            work_df["Rework Duration"] = 0.0

        if "Total Work Duration" not in work_df.columns:
            work_df["Total Work Duration"] = (
                work_df["New Work Duration"] + work_df["Rework Duration"]
            )

        if effective_users:
            work_df = work_df[work_df["User"].isin(effective_users)]

        active_work = work_df[work_df["Total Work Duration"] > 0].copy()
        
        # Use the RawID natively provided by b_df or get_todays_work_df
        if "RawID" in active_work.columns:
            active_work["IDs"] = active_work["RawID"]
        else:
            active_work["IDs"] = ""
    else:
        active_work = pd.DataFrame()

    return build_error_rework_chart(active_work, chart_date_label, is_dark)


# ── Helper: render tab content ───────────────────────────────────────────
def _render_tab(
    active_tab,
    is_dark,
    is_filtering,
    effective_users,
    end_date,
    force_refresh,
):
    """Render the active tab's table content."""
    if active_tab in ("tab-today", "tab-yesterday"):
        target_date = (
            datetime.now().strftime("%Y-%m-%d")
            if active_tab == "tab-today"
            else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        )
        today_df = dm.get_todays_work_df(
            target_date=target_date, force_refresh=force_refresh
        )
        if is_filtering and effective_users and not today_df.empty:
            today_df = today_df[today_df["User"].isin(effective_users)]
        if not today_df.empty:
            for col in ["Total Duration", "New Videos (First Time)", "Reworks"]:
                if col in today_df.columns:
                    today_df[col] = today_df[col].apply(format_seconds)
            totals = today_df.select_dtypes(include=["number"]).sum()
            total_row = {"User": "TOTAL", "Total Tasks": int(totals.get("Total Tasks", 0))}
            raw_full_df = dm.get_todays_work_df(
                target_date=target_date, force_refresh=False
            )
            if is_filtering and effective_users:
                raw_full_df = raw_full_df[
                    raw_full_df["User"].isin(effective_users)
                ]
            for col in ["Total Duration", "New Videos (First Time)", "Reworks"]:
                if col in raw_full_df.columns:
                    total_seconds = (
                        raw_full_df[col].sum() if not raw_full_df.empty else 0
                    )
                    total_row[col] = format_seconds(total_seconds)
            if "Working Hours" in today_df.columns:
                total_wh = raw_full_df["Working Hours Seconds"].sum() if "Working Hours Seconds" in raw_full_df.columns else 0
                total_row["Working Hours"] = format_seconds(total_wh) if total_wh > 0 else "-"
            if "Rework %" in today_df.columns:
                tot_dur = raw_full_df["Total Duration"].sum() if not raw_full_df.empty else 0
                tot_rew = raw_full_df["Reworks"].sum() if not raw_full_df.empty else 0
                total_row["Rework %"] = f"{round(tot_rew / tot_dur * 100, 1)}%" if tot_dur > 0 else "0.0%"
            if "RawID" in today_df.columns:
                total_row["RawID"] = ""
            if "Working Hours Seconds" in today_df.columns:
                today_df = today_df.drop(columns=["Working Hours Seconds"])
            today_df = pd.concat(
                [today_df, pd.DataFrame([total_row])], ignore_index=True
            )
        return create_table(today_df, is_dark)

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
