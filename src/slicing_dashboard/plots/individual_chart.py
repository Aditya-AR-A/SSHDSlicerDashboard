"""
Individual completed-hours horizontal bar chart builder.
"""

import plotly.express as px
import plotly.graph_objects as go
from slicing_dashboard.plots.guardrails import safe_chart, checked_frame, pad_hour_axis, hour_total_label

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    USER_COLORS,
    format_seconds,
    theme_ctx,
)


@safe_chart
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
    breakdown_df = checked_frame(breakdown_df, ['User', 'Completed Duration'], ['Completed Duration'])

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
    if "Completed Duration" in df.columns:
        df = df.groupby('User', as_index=False)['Completed Duration'].sum(min_count=1)

    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No completed work active",
            showarrow=False,
            font={"size": 14, "color": t["font_color"]},
        )
        fig.update_layout(
            template=t["template"],
            paper_bgcolor=t["bg_color"],
            plot_bgcolor=t["bg_color"],
            font=dict(family="Inter, sans-serif", color=t["font_color"]),
            height=CHART_HEIGHT,
        )
        return fig

    df["Formatted Duration"] = df["Completed Duration"].apply(format_seconds)
    df['Total Hours Label'] = (df['Completed Duration'] / 3600).apply(hour_total_label)
    df = df.sort_values(by="Completed Duration", ascending=False)
    max_hours = (df["Completed Duration"] / 3600).max()
    max_range = max_hours * 1.15 if (max_hours and max_hours > 0) else 1

    fig = px.bar(
        df,
        x="User",
        y=df["Completed Duration"] / 3600,
        orientation="v",
        title=f"Completed Hours ({start_date} to {end_date})",
        template=t["template"],
        color="User",
        color_discrete_map=USER_COLORS,
        text="Total Hours Label",
        custom_data=["Formatted Duration"],
    )
    fig.update_layout(
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=12, color=t["font_color"]),
        title_font=dict(size=15, weight="bold"),
        xaxis_title="",
        xaxis=dict(automargin=True, showgrid=False, zeroline=False, tickfont=dict(size=11, weight="bold")),
        yaxis_title="Hours",
        yaxis=dict(range=[0, max_range], automargin=True, showgrid=True, gridcolor="rgba(128,128,128,0.2)", zeroline=False, tickfont=dict(size=11)),
        height=CHART_HEIGHT,
        margin=dict(l=40, r=20, t=40, b=35),
        bargap=0.35,
        clickmode="event+select",
        showlegend=False,
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"], font_size=13),
    )
    fig.update_traces(
        textposition="outside",
        textfont=dict(size=10, weight="bold"),
        hovertemplate="User: <b>%{x}</b><br>Completed: <b>%{customdata[0]}</b> (%{y:.2f} hrs)<extra></extra>",
    )
    return pad_hour_axis(fig)
