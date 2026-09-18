"""
Individual completed-hours horizontal bar chart builder.
"""

import plotly.express as px
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    USER_COLORS,
    format_seconds,
    theme_ctx,
)


def build_individual_chart(
    breakdown_df,
    start_date: str,
    end_date: str,
    is_dark: bool,
) -> go.Figure:
    """Build horizontal bar chart of completed hours per user.

    Parameters
    ----------
    breakdown_df : DataFrame with 'User' and 'Completed Duration' columns.
                   Should already be filtered to selected users.
    start_date, end_date : str for the title
    is_dark : bool
    """
    t = theme_ctx(is_dark)

    if breakdown_df.empty:
        fig = go.Figure()
        fig.update_layout(
            template=t["template"],
            paper_bgcolor=t["bg_color"],
            plot_bgcolor=t["bg_color"],
            font=dict(family="Inter, sans-serif", color=t["font_color"]),
            height=CHART_HEIGHT,
        )
        return fig

    df = breakdown_df.copy()
    df["Formatted Duration"] = df["Completed Duration"].apply(format_seconds)
    df = df.sort_values(by="Completed Duration", ascending=True)
    max_hours = (df["Completed Duration"] / 3600).max()
    max_range = max_hours * 1.15 if (max_hours and max_hours > 0) else 1

    fig = px.bar(
        df,
        x=df["Completed Duration"] / 3600,
        y="User",
        orientation="h",
        title=f"Completed Hours ({start_date} to {end_date})",
        template=t["template"],
        color="User",
        color_discrete_map=USER_COLORS,
        text="Formatted Duration",
        custom_data=["Formatted Duration"],
    )
    fig.update_layout(
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=11, color=t["font_color"]),
        title_font=dict(size=14, weight="bold"),
        xaxis_title="Hours",
        xaxis=dict(range=[0, max_range], automargin=True),
        yaxis_title="",
        yaxis=dict(automargin=True),
        height=CHART_HEIGHT,
        margin=dict(l=10, r=20, t=40, b=35),
        bargap=0.3,
        clickmode="event+select",
        showlegend=False,
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"]),
    )
    fig.update_traces(
        textposition="outside",
        hovertemplate="User: %{y}<br>Duration: %{customdata[0]}<extra></extra>",
    )
    return fig
