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
                 'User', 'Stage', 'Duration', 'Count', and optionally 'IDs'.
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
    if "IDs" not in df.columns:
        df["IDs"] = ""

    max_hours = (df.groupby("User")["Duration"].sum() / 3600).max() if not df.empty else 0
    max_range = max_hours * 1.15 if (max_hours and max_hours > 0) else 1

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
        custom_data=["Formatted Duration", "Stage", "Count", "IDs"],
    )
    fig.update_layout(
        template=t["template"],
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=12, color=t["font_color"]),
        title_font=dict(size=15, weight="bold"),
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
            font=dict(size=11),
        ),
        hoverlabel=dict(
            bgcolor=t["hover_bg"],
            font_color=t["hover_fg"],
            font_size=13,
        ),
        yaxis_title="Hours",
        yaxis=dict(
            range=[0, max_range],
            automargin=True,
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
            zeroline=False,
            tickfont=dict(size=11),
        ),
        xaxis_title="",
        xaxis=dict(automargin=True, tickfont=dict(size=12, weight="bold")),
    )
    fig.update_traces(
        textposition="auto",
        textfont=dict(size=10, weight="bold"),
        constraintext="none",
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Stage: %{customdata[1]}<br>"
            "Duration: <b>%{customdata[0]}</b><br>"
            "Tasks: %{customdata[2]}<br>"
            "Slicer IDs: %{customdata[3]}<extra></extra>"
        ),
    )
    return fig
