"""
Assigned videos horizontal bar chart builder.
"""

import plotly.express as px
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    format_seconds,
    theme_ctx,
)


def build_assigned_chart(assigned_df, is_dark: bool) -> go.Figure:
    """Build horizontal stacked bar chart of assigned videos.

    Parameters
    ----------
    assigned_df : DataFrame filtered to assigned stages, with columns
                  'User', 'Stage', 'Duration', and optionally 'Count'.
    is_dark : bool
    """
    t = theme_ctx(is_dark)

    if assigned_df.empty:
        fig = go.Figure()
        fig.update_layout(
            template=t["template"],
            paper_bgcolor=t["bg_color"],
            plot_bgcolor=t["bg_color"],
            font=dict(family="Inter, sans-serif", color=t["font_color"]),
            height=CHART_HEIGHT,
        )
        return fig

    df = assigned_df.copy()
    df["Formatted Duration"] = df["Duration"].apply(format_seconds)
    if "Count" not in df.columns:
        df["Count"] = 0
    df = df.sort_values(by="Duration", ascending=True)

    fig = px.bar(
        df,
        x=df["Duration"] / 3600,
        y="User",
        orientation="h",
        color="Stage",
        title="Assigned Videos (Hours)",
        color_discrete_map={
            "New Assigned": "#10B981",
            "Rework Assigned": "#F43F5E",
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
        margin=dict(l=10, r=50, t=45, b=55),
        bargap=0.3,
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
        xaxis_title="Hours",
        yaxis_title="",
    )
    fig.update_traces(
        textposition="inside",
        textfont=dict(size=9),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Stage: %{customdata[1]}<br>"
            "Duration: %{customdata[0]}<br>"
            "Tasks: %{customdata[2]}<extra></extra>"
        ),
    )
    return fig
