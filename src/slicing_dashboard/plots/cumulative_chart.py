"""
Cumulative completed-hours line chart builder.
"""

import pandas as pd
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    USER_COLORS,
    format_seconds,
    theme_ctx,
)


def build_cumulative_chart(
    cumulative_df,
    start_date: str,
    is_dark: bool,
    effective_users: list | None = None,
    is_filtering: bool = False,
) -> go.Figure:
    """Build the cumulative completed hours area/line chart.

    Parameters
    ----------
    cumulative_df : DataFrame with a 'Date' column and one column per user
    start_date : str used in the title
    is_dark : bool
    effective_users : list of selected users (or None for all)
    is_filtering : bool whether user filtering is active
    """
    t = theme_ctx(is_dark)
    fig = go.Figure()

    if not cumulative_df.empty:
        for col in cumulative_df.columns:
            if col == "Date":
                continue
            if is_filtering and effective_users and col not in effective_users:
                continue
            color = USER_COLORS.get(col, "#999999")
            formatted = [format_seconds(s) for s in cumulative_df[col]]
            fig.add_trace(
                go.Scatter(
                    x=cumulative_df["Date"],
                    y=cumulative_df[col] / 3600,
                    mode="lines+markers",
                    name=col,
                    line=dict(color=color, shape="spline"),
                    marker=dict(color=color, size=5),
                    customdata=formatted,
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        f"User: {col}<br>"
                        "Duration: %{customdata}<extra></extra>"
                    ),
                )
            )

    max_hours = 0.0
    if not cumulative_df.empty:
        user_cols = [
            c for c in cumulative_df.columns
            if c != "Date" and (not is_filtering or not effective_users or c in effective_users)
        ]
        if user_cols:
            max_val = cumulative_df[user_cols].max().max()
            max_hours = (max_val / 3600) if (max_val and not pd.isna(max_val)) else 0.0
    max_range = max_hours * 1.15 if max_hours > 0 else 1

    fig.update_layout(
        title=f"Cumulative Completed Hours (From {start_date})",
        template=t["template"],
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=12, color=t["font_color"]),
        title_font=dict(size=15, weight="bold"),
        xaxis_title="Date",
        yaxis_title="Hours",
        hovermode="closest",
        height=CHART_HEIGHT,
        margin=dict(l=40, r=15, t=40, b=35),
        showlegend=False,
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"], font_size=13),
        xaxis=dict(showgrid=False, zeroline=False, automargin=True, tickfont=dict(size=11)),
        yaxis=dict(
            range=[0, max_range],
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
            zeroline=False,
            automargin=True,
            tickfont=dict(size=11),
        ),
    )
    return fig
