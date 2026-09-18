"""
New Work + Rework stacked bar chart builder.
"""

import pandas as pd
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    format_seconds,
    theme_ctx,
)


def build_error_rework_chart(
    active_work_df,
    chart_date_label: str,
    is_dark: bool,
) -> go.Figure:
    """Build stacked bar chart of new work vs rework.

    Parameters
    ----------
    active_work_df : DataFrame with columns 'User', 'New Work Duration',
                     'Rework Duration', 'Total Work Duration' — only rows
                     where Total Work Duration > 0.
    chart_date_label : str for the title
    is_dark : bool
    """
    t = theme_ctx(is_dark)

    if active_work_df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text=f"No work recorded ({chart_date_label})",
            showarrow=False,
            font={"size": 14, "color": t["font_color"]},
        )
        fig.update_layout(title=f"New Work + Rework ({chart_date_label})")
    else:
        df = active_work_df.sort_values("Total Work Duration", ascending=False)

        new_hours = df["New Work Duration"] / 3600
        rework_hours = df["Rework Duration"] / 3600

        new_fmt = df["New Work Duration"].apply(format_seconds)
        rework_fmt = df["Rework Duration"].apply(format_seconds)
        total_fmt = df["Total Work Duration"].apply(format_seconds)
        total_hours = df["Total Work Duration"] / 3600

        new_pct = (
            df["New Work Duration"] / df["Total Work Duration"] * 100
        ).fillna(0)
        rework_pct = (
            df["Rework Duration"] / df["Total Work Duration"] * 100
        ).fillna(0)

        new_color = "#38bdf8" if is_dark else "#0284c7"
        rework_color = "#f97316" if is_dark else "#ea580c"

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                name="New Work",
                x=df["User"],
                y=new_hours,
                marker=dict(
                    color=new_color,
                    line=dict(
                        color="rgba(255,255,255,0.15)"
                        if is_dark
                        else "rgba(0,0,0,0.1)",
                        width=1,
                    ),
                ),
                customdata=list(zip(new_fmt, new_pct, total_fmt, total_hours)),
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "<b>New Work:</b> %{customdata[0]} (%{customdata[1]:.1f}%)<br>"
                    "<b>Total:</b> %{customdata[2]}<extra></extra>"
                ),
            )
        )
        fig.add_trace(
            go.Bar(
                name="Rework",
                x=df["User"],
                y=rework_hours,
                marker=dict(
                    color=rework_color,
                    line=dict(
                        color="rgba(255,255,255,0.15)"
                        if is_dark
                        else "rgba(0,0,0,0.1)",
                        width=1,
                    ),
                ),
                customdata=list(
                    zip(rework_fmt, rework_pct, total_fmt, total_hours)
                ),
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "<b>Rework:</b> %{customdata[0]} (%{customdata[1]:.1f}%)<br>"
                    "<b>Total:</b> %{customdata[2]}<extra></extra>"
                ),
            )
        )
        fig.update_layout(
            barmode="stack",
            title=f"New Work + Rework ({chart_date_label})",
            yaxis_title="Hours",
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=10, color=t["font_color"]),
                bgcolor="rgba(0,0,0,0)",
            ),
        )

    max_hours = total_hours.max() if not df.empty else 0
    max_range = max_hours * 1.15 if (max_hours and max_hours > 0) else 1

    fig.update_layout(
        template=t["template"],
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=11, color=t["font_color"]),
        title_font=dict(size=14, weight="bold"),
        height=CHART_HEIGHT,
        margin=dict(l=40, r=15, t=45, b=35),
        bargap=0.35,
        clickmode="event+select",
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"]),
        xaxis=dict(showgrid=False, zeroline=False, automargin=True),
        yaxis=dict(
            range=[0, max_range],
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
            zeroline=False,
            automargin=True,
        ),
    )
    return fig
