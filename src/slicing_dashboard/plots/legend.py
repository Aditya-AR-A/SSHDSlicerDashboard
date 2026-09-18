"""
Universal user legend (clickable filter) builder.
"""

import plotly.graph_objects as go

from slicing_dashboard.plots.theme import CHART_HEIGHT, USER_COLORS, theme_ctx


def build_legend_figure(
    available_users: list,
    current_selection: list,
    is_dark: bool,
) -> go.Figure:
    """Build a figure that serves as a clickable user-filter legend.

    Parameters
    ----------
    available_users : list of all user names
    current_selection : list of currently selected user names
    is_dark : bool
    """
    t = theme_ctx(is_dark)
    fig = go.Figure()

    for u in available_users:
        is_visible = u in current_selection
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                name=u,
                mode="markers",
                marker=dict(color=USER_COLORS.get(u, "#999"), size=10),
                showlegend=True,
                visible=True if is_visible else "legendonly",
            )
        )

    fig.update_layout(
        template=t["template"],
        showlegend=True,
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="center",
            x=0.5,
            font=dict(color=t["font_color"], size=11),
        ),
        height=CHART_HEIGHT,
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        hovermode=False,
    )
    return fig
