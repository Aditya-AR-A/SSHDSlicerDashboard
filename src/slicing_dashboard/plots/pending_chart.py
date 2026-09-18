"""
Pending reviews stacked bar chart builder.
"""

import plotly.express as px
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    format_seconds,
    theme_ctx,
)


def build_pending_chart(pending_df, is_dark: bool) -> go.Figure:
    """Build stacked bar chart of pending reviews by stage.

    Parameters
    ----------
    pending_df : DataFrame filtered to pending stages, with columns
                 'User', 'Stage', 'Duration', and optionally 'Count'.
    is_dark : bool
    """
    t = theme_ctx(is_dark)

    if pending_df.empty:
        fig = go.Figure()
        fig.update_layout(
            template=t["template"],
            paper_bgcolor=t["bg_color"],
            plot_bgcolor=t["bg_color"],
            font=dict(family="Inter, sans-serif", color=t["font_color"]),
            height=CHART_HEIGHT,
        )
        return fig

    df = pending_df.copy()
    df["Formatted Duration"] = df["Duration"].apply(format_seconds)
    if "Count" not in df.columns:
        df["Count"] = 0

    fig = px.bar(
        df,
        x="User",
        y=df["Duration"] / 3600,
        color="Stage",
        title="Pending Reviews by Stage (Hours)",
        color_discrete_map={
            "Pending Leader": "#F59E0B",
            "Pending Auditor": "#8B5CF6",
            "Pending Admin": "#06B6D4",
        },
        text="Formatted Duration",
        barmode="stack",
        custom_data=["Formatted Duration", "Stage", "Count"],
    )
    fig.update_layout(
        template=t["template"],
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=11, color=t["font_color"]),
        title_font=dict(size=14, weight="bold"),
        height=CHART_HEIGHT,
        margin=dict(l=40, r=15, t=45, b=55),
        bargap=0.35,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
        ),
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"]),
        yaxis_title="Hours",
        xaxis_title="",
    )
    fig.update_traces(
        textposition="inside",
        textfont=dict(size=9),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Stage: %{customdata[1]}<br>"
            "Duration: %{customdata[0]}<br>"
            "Tasks: %{customdata[2]}<extra></extra>"
        ),
    )
    return fig
