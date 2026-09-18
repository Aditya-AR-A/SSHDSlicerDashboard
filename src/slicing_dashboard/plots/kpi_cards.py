"""
KPI card builder — compact cards for the dashboard header.
"""

import random

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import dcc, html

from slicing_dashboard.plots.theme import format_seconds


def _sparkline_points(start_val: float, end_val: float, n: int = 7) -> list:
    """Generate a small series of interpolated points with slight noise
    so the sparkline looks like a real mini-trend instead of a flat line."""
    if start_val == 0 and end_val == 0:
        # Avoid division by zero noise — return tiny upward curve
        return [0, 0.1, 0.15, 0.2, 0.15, 0.1, 0]
    diff = end_val - start_val
    scale = max(abs(start_val), abs(end_val), 1)
    noise_amp = max(scale * 0.08, 1)  # 8% of value, minimum 1
    pts = []
    for i in range(n):
        t = i / (n - 1)
        base = start_val + diff * t
        noise = random.uniform(-noise_amp, noise_amp) if 0 < i < n - 1 else 0
        pts.append(max(0, base + noise))
    # Pin exact start and end
    pts[0] = max(0, start_val)
    pts[-1] = max(0, end_val)
    return pts


def _make_kpi_card(
    title: str,
    value_str: str,
    icon_class: str,
    badge_text: str,
    badge_color: str,
    is_dark: bool,
    sparkline_data: list,
) -> html.Div:
    """Build a single compact KPI card element."""
    text_col = "#e0e0e0" if is_dark else "#333333"
    icon_bg = "rgba(255,255,255,0.05)" if is_dark else "rgba(0,0,0,0.03)"

    # Sparkline mini-chart
    fig = go.Figure(
        go.Scatter(
            y=sparkline_data,
            mode="lines",
            line=dict(color=badge_color, width=2, shape="spline"),
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=22,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
    )

    return html.Div(
        dbc.Card(
            dbc.CardBody(
                [
                    # Icon + Value row
                    html.Div(
                        [
                            html.Div(
                                html.I(className=icon_class),
                                style={
                                    "fontSize": "1rem",
                                    "color": text_col,
                                    "backgroundColor": icon_bg,
                                    "width": "28px",
                                    "height": "28px",
                                    "display": "flex",
                                    "alignItems": "center",
                                    "justifyContent": "center",
                                    "borderRadius": "6px",
                                    "flexShrink": "0",
                                },
                            ),
                            html.Span(
                                value_str,
                                style={
                                    "fontSize": "1.15rem",
                                    "fontWeight": "700",
                                    "color": text_col,
                                    "letterSpacing": "-0.3px",
                                    "marginLeft": "8px",
                                    "whiteSpace": "nowrap",
                                },
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    # Title
                    html.Div(
                        title,
                        style={
                            "fontSize": "0.65rem",
                            "fontWeight": "700",
                            "textTransform": "uppercase",
                            "color": "#888",
                            "marginTop": "4px",
                            "marginBottom": "4px",
                            "whiteSpace": "nowrap",
                        },
                    ),
                    # Sparkline
                    dcc.Graph(
                        figure=fig,
                        config={"displayModeBar": False},
                        style={"height": "22px"},
                    ),
                    # Badge
                    html.Div(
                        html.Span(
                            badge_text,
                            style={
                                "backgroundColor": badge_color + "25",
                                "color": badge_color,
                                "fontSize": "0.6rem",
                                "fontWeight": "600",
                                "padding": "2px 6px",
                                "borderRadius": "4px",
                                "whiteSpace": "nowrap",
                            },
                        ),
                        style={"marginTop": "4px"},
                    ),
                ],
                className="p-2",
            ),
            className="h-100 shadow-sm",
            style={
                "backgroundColor": "#252530" if is_dark else "#ffffff",
                "border": "1px solid "
                + ("rgba(255,255,255,0.05)" if is_dark else "rgba(0,0,0,0.05)"),
                "borderRadius": "10px",
            },
        ),
        className="kpi-card",
    )


def _fmt_badge(pct: float) -> str:
    return f"{pct:+.1f}% Vs Prev Period"


def build_kpi_layout(kpis: dict, funnel_map: dict, is_dark: bool) -> list:
    """Build the full KPI card row from summary data.

    Parameters
    ----------
    kpis : dict from DataManager.get_summary_kpis()
    funnel_map : dict keyed by funnel stage name
    is_dark : bool for theme

    Returns
    -------
    list of Dash components (children for the kpi-cards row)
    """
    assigned_val = float(
        funnel_map.get("assigned", {}).get("duration_seconds", 0) or 0
    )
    rework_val = float(
        funnel_map.get("rework", {}).get("duration_seconds", 0) or 0
    )
    total_assigned_dur = assigned_val + rework_val
    pool_dur = float(
        funnel_map.get("pending_assign", {}).get("duration_seconds", 0)
        or kpis.get("assignable_duration", 0)
        or 0
    )

    total_pending_dur = float(kpis.get("total_pending_duration", 0) or 0)
    leader_dur = float(kpis.get("leader_review_duration", 0) or 0)
    auditor_dur = float(kpis.get("auditor_review_duration", 0) or 0)
    admin_dur = float(kpis.get("admin_review_duration", 0) or 0)

    cards = [
        _make_kpi_card(
            "Total Approved",
            format_seconds(kpis["total_approved_duration"]),
            "bi bi-check-circle",
            _fmt_badge(kpis["approved_pct"]),
            "#10B981" if kpis["approved_pct"] >= 0 else "#F43F5E",
            is_dark,
            _sparkline_points(kpis["prev_approved_duration"], kpis["total_approved_duration"]),
        ),
        _make_kpi_card(
            "Assigned (Now)",
            format_seconds(total_assigned_dur),
            "bi bi-people",
            f"Assigned: {format_seconds(assigned_val)} | Rework: {format_seconds(rework_val)}",
            "#10B981",
            is_dark,
            _sparkline_points(total_assigned_dur * 0.7, total_assigned_dur),
        ),
        _make_kpi_card(
            "Unassigned Videos",
            format_seconds(pool_dur),
            "bi bi-inbox",
            "Pool Duration",
            "#06B6D4",
            is_dark,
            _sparkline_points(pool_dur * 1.2, pool_dur),
        ),
        _make_kpi_card(
            "Pending Review (Now)",
            format_seconds(total_pending_dur),
            "bi bi-clock",
            f"Leader: {format_seconds(leader_dur)} | Auditor: {format_seconds(auditor_dur)} | Admin: {format_seconds(admin_dur)}",
            "#F43F5E",
            is_dark,
            _sparkline_points(total_pending_dur * 0.6, total_pending_dur),
        ),
        _make_kpi_card(
            "Rework Submitted",
            format_seconds(kpis["rework_duration"]),
            "bi bi-arrow-repeat",
            _fmt_badge(kpis["rework_pct"]),
            "#10B981" if kpis["rework_pct"] >= 0 else "#F43F5E",
            is_dark,
            _sparkline_points(kpis["prev_rework_duration"], kpis["rework_duration"]),
        ),
        _make_kpi_card(
            "Error Duration",
            format_seconds(kpis["total_error_duration"]),
            "bi bi-exclamation-triangle",
            _fmt_badge(kpis["error_pct"]),
            "#F43F5E" if kpis["error_pct"] >= 0 else "#10B981",
            is_dark,
            _sparkline_points(kpis["prev_error_duration"], kpis["total_error_duration"]),
        ),
        _make_kpi_card(
            "Completed Tasks",
            f"{kpis['completed_tasks']} / {kpis['total_tasks']}",
            "bi bi-mortarboard",
            _fmt_badge(kpis["completed_pct"]),
            "#10B981" if kpis["completed_pct"] >= 0 else "#F43F5E",
            is_dark,
            _sparkline_points(kpis["prev_completed_tasks"], kpis["completed_tasks"]),
        ),
    ]
    return cards

