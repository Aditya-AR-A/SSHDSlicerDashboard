import dash
from dash import dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta
import subprocess
from pathlib import Path
from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.config import PROJECT_ROOT

USER_COLORS = {
    "Aditya": "#636EFA",
    "Komal": "#EF553B",
    "Priya": "#00CC96",
    "Rajni": "#AB63FA",
    "Ranjeeta": "#FFA15A",
    "Riya": "#19D3F3",
    "Sanddep": "#FF6692",
    "Admin": "#B6E880",
    "Test": "#FF97FF",
    "Dep": "#FECB52",
}
dm = DataManager()
end_dt = datetime.now()
start_dt = end_dt - timedelta(days=30)
default_start = start_dt.strftime("%Y-%m-%d")
default_end = end_dt.strftime("%Y-%m-%d")


def format_seconds(seconds):
    if pd.isna(seconds) or seconds is None:
        return "00:00"
    seconds = int(seconds)
    h = seconds // 3600
    m = seconds % 3600 // 60
    return f"{h:02d}:{m:02d}"


app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
try:
    all_users_df = dm.get_user_breakdown_df("2026-07-01", default_end)
    available_users = (
        sorted(all_users_df["User"].unique().tolist()) if not all_users_df.empty else []
    )
except Exception:
    available_users = []
app.layout = html.Div(
    id="main-container",
    className="theme-dark",
    children=[
        dbc.Container(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            html.H2(
                                "Slicing Performance & Settlement",
                                className="fw-bold m-0 text-primary",
                            ),
                            width="auto",
                            className="me-auto",
                        ),
                        dbc.Col(
                            dbc.ButtonGroup(
                                [
                                    dbc.Button(
                                        "Today",
                                        id="btn-today",
                                        color="outline-primary",
                                        size="sm",
                                    ),
                                    dbc.Button(
                                        "7 Days",
                                        id="btn-7d",
                                        color="outline-primary",
                                        size="sm",
                                    ),
                                    dbc.Button(
                                        "Month",
                                        id="btn-month",
                                        color="outline-primary",
                                        size="sm",
                                    ),
                                ],
                                className="me-3 mt-1",
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
                                                className="date-filter-label me-2 fw-bold",
                                            ),
                                            dbc.Input(
                                                id="date-from",
                                                type="date",
                                                value=default_start,
                                                size="sm",
                                                className="date-input-custom",
                                            ),
                                        ],
                                        className="d-flex align-items-center me-3",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "To",
                                                className="date-filter-label me-2 fw-bold",
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
                                className="d-flex align-items-center date-filter-group me-3",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button(
                                html.I(className="bi bi-moon-stars"),
                                id="theme-toggle",
                                color="link",
                                className="text-decoration-none fs-4 text-primary",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dcc.Loading(
                                id="loading-refresh",
                                type="circle",
                                children=html.Div(
                                    id="refresh-status",
                                    className="text-success small fw-bold mt-1 me-3",
                                ),
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button(
                                html.I(className="bi bi-arrow-clockwise"),
                                id="refresh-btn",
                                color="primary",
                                size="md",
                                title="Refresh",
                            ),
                            width="auto",
                        ),
                    ],
                    className="mb-4 align-items-center glass-panel p-3 rounded shadow-sm d-flex justify-content-between",
                    style={"position": "relative", "zIndex": 1050},
                ),
                dbc.Row(id="kpi-cards", className="mb-4"),
                dcc.Store(id="selected-users-store", data=available_users),
                dcc.Interval(
                    id="auto-refresh-interval", interval=5 * 60 * 1000, n_intervals=0
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Graph(
                                id="cumulative-chart",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=7,
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="individual-chart",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=4,
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="universal-legend",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=1,
                        ),
                    ],
                    className="mb-4",
                    style={"display": "flex", "alignItems": "stretch"},
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Graph(
                                id="pending-chart",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=5,
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="error-rework-chart",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=4,
                        ),
                        dbc.Col(
                            dcc.Graph(
                                id="assigned-chart",
                                config={"displayModeBar": False},
                                className="glass-panel p-2 rounded shadow-sm",
                                style={"height": "450px"},
                            ),
                            width=12,
                            lg=3,
                        ),
                    ],
                    className="mb-4",
                    style={"display": "flex", "alignItems": "stretch"},
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Tabs(
                                    [
                                        dbc.Tab(
                                            label="Slice Data Overview",
                                            tab_id="tab-overview",
                                        ),
                                        dbc.Tab(
                                            label="Today's Work", tab_id="tab-today"
                                        ),
                                        dbc.Tab(
                                            label="Yesterday's Work",
                                            tab_id="tab-yesterday",
                                        ),
                                        dbc.Tab(
                                            label="Settlement Overview (Since Aug 7)",
                                            tab_id="tab-settlement",
                                        ),
                                    ],
                                    id="tabs",
                                    active_tab="tab-overview",
                                    className="mb-3",
                                ),
                                html.Div(
                                    id="tabs-content",
                                    className="glass-panel p-3 rounded shadow-sm",
                                ),
                            ]
                        )
                    ]
                ),
            ],
            fluid=True,
            className="p-4",
        )
    ],
)
app.clientside_callback(
    dash.ClientsideFunction(namespace="clientside", function_name="toggleTheme"),
    Output("main-container", "className"),
    Input("theme-toggle", "n_clicks"),
)


@app.callback(
    [Output("date-from", "value"), Output("date-to", "value")],
    [
        Input("btn-today", "n_clicks"),
        Input("btn-7d", "n_clicks"),
        Input("btn-month", "n_clicks"),
    ],
    prevent_initial_call=True,
)
def quick_filters(btn_today, btn_7d, btn_month):
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    button_id = ctx.triggered[0]["prop_id"].split(".")[0]
    end = datetime.now()
    if button_id == "btn-today":
        start = end
    elif button_id == "btn-7d":
        start = end - timedelta(days=7)
    elif button_id == "btn-month":
        start = end.replace(day=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


@app.callback(
    [
        Output("kpi-cards", "children"),
        Output("universal-legend", "figure"),
        Output("cumulative-chart", "figure"),
        Output("error-rework-chart", "figure"),
        Output("individual-chart", "figure"),
        Output("pending-chart", "figure"),
        Output("assigned-chart", "figure"),
        Output("tabs-content", "children"),
        Output("refresh-status", "children"),
        Output("theme-toggle", "children"),
        Output("selected-users-store", "data"),
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
    is_dark = True if theme_clicks is None else theme_clicks % 2 == 0
    toggle_label = (
        html.I(className="bi bi-moon-stars")
        if is_dark
        else html.I(className="bi bi-sun")
    )
    if not start_date or not end_date:
        raise dash.exceptions.PreventUpdate
    ctx = dash.callback_context
    force_refresh = False
    triggered_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None
    if triggered_id in ["refresh-btn", "auto-refresh-interval"]:
        force_refresh = True
        try:
            import subprocess

            subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "slicing_dashboard.cli",
                    "run",
                    "--daily",
                ],
                cwd=str(PROJECT_ROOT),
                check=True,
            )
        except Exception as e:
            print("Fast sync error:", e)
    current_selection = (
        list(stored_users) if stored_users is not None else list(available_users)
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
                    elif (vis == True or vis is None) and user not in current_selection:
                        current_selection.append(user)
    if triggered_id in ["individual-chart", "error-rework-chart"]:
        click_data = ind_click if triggered_id == "individual-chart" else err_click
        if click_data and "points" in click_data:
            pt = click_data["points"][0]
            clicked_user = pt.get("x") or pt.get("label")
            if clicked_user:
                if clicked_user in current_selection:
                    current_selection.remove(clicked_user)
                else:
                    current_selection.append(clicked_user)
    effective_users = current_selection if current_selection else None
    theme_template = "plotly_dark" if is_dark else "plotly_white"
    font_color = "#e0e0e0" if is_dark else "#333333"
    bg_color = "rgba(0,0,0,0)"
    hover_bg = "#2a2a35" if is_dark else "#f0f0f0"
    hover_fg = "#ffffff" if is_dark else "#111111"

    def create_table(df):
        if df.empty:
            return html.Div("No data available")
        header_bg = "rgba(40, 40, 40, 0.9)" if is_dark else "rgba(230, 230, 230, 0.9)"
        cell_bg = "rgba(20, 20, 20, 0.5)" if is_dark else "rgba(255, 255, 255, 0.5)"
        odd_bg = "rgba(30, 30, 30, 0.5)" if is_dark else "rgba(240, 240, 240, 0.5)"
        border_col = "rgba(255,255,255,0.05)" if is_dark else "rgba(0,0,0,0.05)"
        return dash_table.DataTable(
            data=df.to_dict("records"),
            columns=[{"name": str(i), "id": str(i)} for i in df.columns],
            style_header={
                "backgroundColor": header_bg,
                "color": font_color,
                "fontWeight": "bold",
                "border": "none",
                "textAlign": "left",
                "fontFamily": "Inter, sans-serif",
                "fontSize": "15px",
            },
            style_cell={
                "backgroundColor": cell_bg,
                "color": font_color,
                "border": f"1px solid {border_col}",
                "padding": "12px",
                "textAlign": "left",
                "fontFamily": "Inter, sans-serif",
                "fontSize": "14px",
            },
            style_data_conditional=[
                {"if": {"row_index": "odd"}, "backgroundColor": odd_bg}
            ],
            style_table={"borderRadius": "10px", "overflow": "hidden"},
        )

    raw_data = dm.fetch_dashboard_data(start_date, end_date, force_refresh)
    kpis = dm.get_summary_kpis(start_date, end_date, force_refresh)
    total_pending_dur = raw_data.get("metrics", {}).get(
        "review_pending_duration_seconds", 0
    )
    total_assigned_dur = raw_data.get("metrics", {}).get(
        "slice_backlog_duration_seconds", 0
    )

    def make_kpi_card(
        title,
        value_str,
        icon_class,
        badge_text,
        badge_color,
        is_dark_mode,
        sparkline_data,
    ):
        bg_col = "#252530" if is_dark_mode else "#ffffff"
        text_col = "#e0e0e0" if is_dark_mode else "#333333"
        icon_bg = "rgba(255,255,255,0.05)" if is_dark_mode else "rgba(0,0,0,0.03)"
        border_col = "rgba(255,255,255,0.05)" if is_dark_mode else "rgba(0,0,0,0.05)"
        fig = go.Figure(
            go.Scatter(
                y=sparkline_data,
                mode="lines",
                line=dict(color=badge_color, width=2.5, shape="spline"),
                hoverinfo="skip",
            )
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=0, b=0),
            height=30,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, zeroline=False, visible=False),
            yaxis=dict(showgrid=False, zeroline=False, visible=False),
        )
        return dbc.Col(
            dbc.Card(
                [
                    dbc.CardBody(
                        [
                            dbc.Row(
                                [
                                    dbc.Col(
                                        html.Div(
                                            html.I(className=icon_class),
                                            style={
                                                "fontSize": "1.3rem",
                                                "color": text_col,
                                                "backgroundColor": icon_bg,
                                                "width": "35px",
                                                "height": "35px",
                                                "display": "flex",
                                                "alignItems": "center",
                                                "justifyContent": "center",
                                                "borderRadius": "8px",
                                            },
                                        ),
                                        width="auto",
                                        className="pe-1",
                                    ),
                                    dbc.Col(
                                        html.H3(
                                            value_str,
                                            className="mb-0 fw-bold",
                                            style={
                                                "fontSize": "1.6rem",
                                                "color": text_col,
                                                "letterSpacing": "-0.5px",
                                            },
                                        ),
                                        className="ps-2",
                                    ),
                                ],
                                className="align-items-center mb-1",
                            ),
                            html.H6(
                                title,
                                className="text-muted fw-bold mb-3 mt-1",
                                style={
                                    "fontSize": "0.75rem",
                                    "textTransform": "uppercase",
                                },
                            ),
                            html.Div(
                                dcc.Graph(
                                    figure=fig,
                                    config={"displayModeBar": False},
                                    style={"height": "30px"},
                                ),
                                style={"marginBottom": "12px"},
                            ),
                            html.Div(
                                [
                                    html.Span(
                                        badge_text,
                                        className="fw-bold px-2 py-1 rounded",
                                        style={
                                            "backgroundColor": badge_color + "25",
                                            "color": badge_color,
                                            "fontSize": "0.7rem",
                                        },
                                    )
                                ]
                            ),
                        ],
                        className="p-3",
                    )
                ],
                className="h-100 shadow-sm",
                style={
                    "backgroundColor": bg_col,
                    "border": f"1px solid {border_col}",
                    "borderRadius": "12px",
                },
            ),
            width=12,
            md=4,
            lg=2,
            className="mb-3 mb-lg-0",
        )

    def fmt_badge(pct):
        return f"{pct:+.1f}% Vs Prev Period"

    assigned_trend = [total_assigned_dur, total_assigned_dur]
    pending_trend = [total_pending_dur, total_pending_dur]
    kpi_layout = [
        make_kpi_card(
            "Total Approved",
            format_seconds(kpis["total_approved_duration"]),
            "bi bi-check-circle",
            fmt_badge(kpis["approved_pct"]),
            "#10B981" if kpis["approved_pct"] >= 0 else "#F43F5E",
            is_dark,
            [kpis["prev_approved_duration"], kpis["total_approved_duration"]],
        ),
        make_kpi_card(
            "Assigned (Now)",
            format_seconds(total_assigned_dur),
            "bi bi-people",
            "Current Backlog",
            "#10B981",
            is_dark,
            assigned_trend,
        ),
        make_kpi_card(
            "Pending Review (Now)",
            format_seconds(total_pending_dur),
            "bi bi-clock",
            "Current Backlog",
            "#F43F5E",
            is_dark,
            pending_trend,
        ),
        make_kpi_card(
            "Rework Submitted",
            format_seconds(kpis["rework_duration"]),
            "bi bi-arrow-repeat",
            fmt_badge(kpis["rework_pct"]),
            "#10B981" if kpis["rework_pct"] >= 0 else "#F43F5E",
            is_dark,
            [kpis["prev_rework_duration"], kpis["rework_duration"]],
        ),
        make_kpi_card(
            "Error Duration",
            format_seconds(kpis["total_error_duration"]),
            "bi bi-exclamation-triangle",
            fmt_badge(kpis["error_pct"]),
            "#F43F5E" if kpis["error_pct"] >= 0 else "#10B981",
            is_dark,
            [kpis["prev_error_duration"], kpis["total_error_duration"]],
        ),
        make_kpi_card(
            "Completed Tasks",
            f"{kpis['completed_tasks']} / {kpis['total_tasks']}",
            "bi bi-mortarboard",
            fmt_badge(kpis["completed_pct"]),
            "#10B981" if kpis["completed_pct"] >= 0 else "#F43F5E",
            is_dark,
            [kpis["prev_completed_tasks"], kpis["completed_tasks"]],
        ),
    ]
    fig_legend = go.Figure()
    for u in available_users:
        is_visible = u in current_selection
        fig_legend.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                name=u,
                mode="markers",
                marker=dict(color=USER_COLORS.get(u, "#999"), size=12),
                showlegend=True,
                visible=True if is_visible else "legendonly",
            )
        )
    fig_legend.update_layout(
        template=theme_template,
        showlegend=True,
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="center",
            x=0.5,
            font=dict(color=font_color, size=15),
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor=bg_color,
        paper_bgcolor=bg_color,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        hovermode=False,
    )
    breakdown_df = dm.get_user_breakdown_df(start_date, end_date, force_refresh)
    if effective_users:
        breakdown_df = breakdown_df[breakdown_df["User"].isin(effective_users)]
    cumulative_df = dm.get_cumulative_df("2026-08-07", end_date, force_refresh)
    fig_cum = go.Figure()
    if not cumulative_df.empty:
        for col in cumulative_df.columns:
            if col != "Date" and (not effective_users or col in effective_users):
                color = USER_COLORS.get(col, "#999999")
                formatted_strs = [format_seconds(s) for s in cumulative_df[col]]
                fig_cum.add_trace(
                    go.Scatter(
                        x=cumulative_df["Date"],
                        y=cumulative_df[col] / 3600,
                        mode="lines+markers",
                        name=col,
                        line=dict(color=color, shape="spline"),
                        marker=dict(color=color),
                        customdata=formatted_strs,
                        hovertemplate="<b>%{x}</b><br>User: "
                        + col
                        + "<br>Duration: %{customdata}<extra></extra>",
                    )
                )
    fig_cum.update_layout(
        title="Cumulative Completed Hours (From Aug 7)",
        template=theme_template,
        plot_bgcolor=bg_color,
        paper_bgcolor=bg_color,
        font=dict(family="Inter, sans-serif", size=16, color=font_color),
        title_font=dict(size=20, weight="bold"),
        xaxis_title="Date",
        yaxis_title="Hours",
        hovermode="closest",
        margin=dict(l=50, r=30, t=60, b=50),
        showlegend=False,
        hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.2)", zeroline=False),
    )
    if not breakdown_df.empty:
        breakdown_df["Formatted Duration"] = breakdown_df["Completed Duration"].apply(
            format_seconds
        )
        fig_ind = px.bar(
            breakdown_df,
            x="User",
            y=breakdown_df["Completed Duration"] / 3600,
            title=f"Completed Hours ({start_date} to {end_date})",
            template=theme_template,
            color="User",
            color_discrete_map=USER_COLORS,
            text="Formatted Duration",
            custom_data=["Formatted Duration"],
        )
        fig_ind.update_layout(
            plot_bgcolor=bg_color,
            paper_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", size=16, color=font_color),
            title_font=dict(size=20, weight="bold"),
            yaxis_title="Hours",
            margin=dict(l=50, r=30, t=60, b=50),
            clickmode="event+select",
            showlegend=False,
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
        fig_ind.update_traces(
            textposition="outside",
            hovertemplate="User: %{x}<br>Duration: %{customdata[0]}<extra></extra>",
        )
        error_df = breakdown_df[["User", "Error Count"]].copy()
        error_df = error_df[error_df["Error Count"] > 0]
        if not error_df.empty:
            fig_err = px.pie(
                error_df,
                values="Error Count",
                names="User",
                hole=0.5,
                title="Errors by User",
                color="User",
                color_discrete_map=USER_COLORS,
            )
            fig_err.update_traces(
                pull=[0.05] * len(error_df),
                hovertemplate="%{label}: %{value} <extra></extra>",
            )
        else:
            fig_err = go.Figure().add_annotation(
                text="No errors found", showarrow=False, font={"size": 18}
            )
            fig_err.update_layout(title="Errors by User")
        fig_err.update_layout(
            template=theme_template,
            plot_bgcolor=bg_color,
            paper_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", size=16, color=font_color),
            title_font=dict(size=20, weight="bold"),
            margin=dict(l=20, r=20, t=60, b=20),
            clickmode="event+select",
            showlegend=False,
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
    else:
        fig_ind = go.Figure().update_layout(
            template=theme_template,
            paper_bgcolor=bg_color,
            plot_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", color=font_color),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
        fig_err = go.Figure().update_layout(
            template=theme_template,
            paper_bgcolor=bg_color,
            plot_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", color=font_color),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
    detailed_df = dm.get_detailed_pending_assigned_df(force_refresh=force_refresh)
    if effective_users and not detailed_df.empty:
        detailed_df = detailed_df[detailed_df["User"].isin(effective_users)]
    pending_stages = ["Pending Leader", "Pending Auditor", "Pending Admin"]
    pending_df = (
        detailed_df[detailed_df["Stage"].isin(pending_stages)]
        if not detailed_df.empty
        else pd.DataFrame()
    )
    if not pending_df.empty:
        pending_df["Formatted Duration"] = pending_df["Duration"].apply(format_seconds)
        fig_pending = px.bar(
            pending_df,
            x="User",
            y=pending_df["Duration"] / 3600,
            color="Stage",
            title="Pending Reviews (Hours)",
            color_discrete_map={
                "Pending Leader": "#F59E0B",
                "Pending Auditor": "#8B5CF6",
                "Pending Admin": "#06B6D4",
            },
            text="Formatted Duration",
            barmode="stack",
            custom_data=["Formatted Duration"],
        )
        fig_pending.update_layout(
            template=theme_template,
            plot_bgcolor=bg_color,
            paper_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", size=16, color=font_color),
            title_font=dict(size=20, weight="bold"),
            margin=dict(l=50, r=30, t=80, b=80),
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.15,
                xanchor="center",
                x=0.5,
                font=dict(size=14),
            ),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
            yaxis_title="Hours",
            xaxis_title="",
        )
        fig_pending.update_traces(
            textposition="inside",
            hovertemplate="Stage: %{data.name}<br>User: %{x}<br>Duration: %{customdata[0]}<extra></extra>",
        )
    else:
        fig_pending = go.Figure().update_layout(
            template=theme_template,
            paper_bgcolor=bg_color,
            plot_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", color=font_color),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
    assigned_stages = ["New Assigned", "Rework Assigned"]
    assigned_df = (
        detailed_df[detailed_df["Stage"].isin(assigned_stages)]
        if not detailed_df.empty
        else pd.DataFrame()
    )
    if not assigned_df.empty:
        assigned_df["Formatted Duration"] = assigned_df["Duration"].apply(
            format_seconds
        )
        fig_assigned = px.bar(
            assigned_df,
            x="User",
            y=assigned_df["Duration"] / 3600,
            color="Stage",
            title="Assigned Videos (Hours)",
            color_discrete_map={
                "New Assigned": "#10B981",
                "Rework Assigned": "#F43F5E",
            },
            text="Formatted Duration",
            barmode="stack",
            custom_data=["Formatted Duration"],
        )
        fig_assigned.update_layout(
            template=theme_template,
            plot_bgcolor=bg_color,
            paper_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", size=16, color=font_color),
            title_font=dict(size=20, weight="bold"),
            margin=dict(l=50, r=30, t=80, b=80),
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.15,
                xanchor="center",
                x=0.5,
                font=dict(size=14),
            ),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
            yaxis_title="Hours",
            xaxis_title="",
        )
        fig_assigned.update_traces(
            textposition="inside",
            hovertemplate="Stage: %{data.name}<br>User: %{x}<br>Duration: %{customdata[0]}<extra></extra>",
        )
    else:
        fig_assigned = go.Figure().update_layout(
            template=theme_template,
            paper_bgcolor=bg_color,
            plot_bgcolor=bg_color,
            font=dict(family="Inter, sans-serif", color=font_color),
            hoverlabel=dict(bgcolor=hover_bg, font_color=hover_fg),
        )
    if active_tab in ("tab-today", "tab-yesterday"):
        target_date = (
            datetime.now().strftime("%Y-%m-%d")
            if active_tab == "tab-today"
            else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        )
        today_df = dm.get_todays_work_df(
            target_date=target_date, force_refresh=force_refresh
        )
        if effective_users and not today_df.empty:
            today_df = today_df[today_df["User"].isin(effective_users)]
        if not today_df.empty:
            for col in ["Total Duration", "New Videos (First Time)", "Reworks"]:
                today_df[col] = today_df[col].apply(format_seconds)
            totals = today_df.select_dtypes(include=["number"]).sum()
            total_row = {"User": "TOTAL", "Total Tasks": totals["Total Tasks"]}
            raw_full_df = dm.get_todays_work_df(
                target_date=target_date, force_refresh=False
            )
            if effective_users:
                raw_full_df = raw_full_df[raw_full_df["User"].isin(effective_users)]
            for col in ["Total Duration", "New Videos (First Time)", "Reworks"]:
                total_seconds = raw_full_df[col].sum() if not raw_full_df.empty else 0
                total_row[col] = format_seconds(total_seconds)
            today_df = pd.concat(
                [today_df, pd.DataFrame([total_row])], ignore_index=True
            )
        tab_content = create_table(today_df)
    elif active_tab == "tab-overview":
        overview_df = dm.get_slice_data_overview_df(
            start_date, end_date, use_raw_names=True
        )
        if effective_users and not overview_df.empty:
            # We must map effective_users (canonical) back to raw usernames?
            # Or just filter if the raw username is in effective_users?
            # For simplicity, if they filter by canonical user, we might miss some raw usernames.
            # Let's map the raw usernames to canonical to check if they should be included.
            filtered_records = []
            for _, row in overview_df.iterrows():
                raw_user = row["Username"]
                # Determine canonical user
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
        tab_content = create_table(overview_df)
    else:
        full_breakdown_df = dm.get_user_breakdown_df(
            start_date, end_date, force_refresh
        )
        if effective_users:
            full_breakdown_df = full_breakdown_df[
                full_breakdown_df["User"].isin(effective_users)
            ]
        settlement_df = dm.get_settlement_df(full_breakdown_df)
        if not settlement_df.empty:
            settlement_df = settlement_df.sort_values(
                by="Remaining Duration", ascending=False
            )
            for col in ["Paid Duration", "Remaining Duration", "Total Duration"]:
                settlement_df[col] = settlement_df[col].apply(format_seconds)
        tab_content = create_table(settlement_df)
    status_msg = f"Updated: {datetime.now().strftime('%H:%M:%S')}"
    return (
        kpi_layout,
        fig_legend,
        fig_cum,
        fig_err,
        fig_ind,
        fig_pending,
        fig_assigned,
        tab_content,
        status_msg,
        toggle_label,
        current_selection,
    )


if __name__ == "__main__":
    app.run(debug=True, port=8050)
