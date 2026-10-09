"""Today-only composition; settlement and overall amounts are separate scopes."""
import plotly.graph_objects as go
from slicing_dashboard.plots.guardrails import safe_chart, finite_number
from slicing_dashboard.plots.theme import theme_ctx
from slicing_dashboard.processing.daily_work import format_video_seconds


@safe_chart(height=360)
def build_work_breakdown_donut(summary, is_dark=True):
    theme = theme_ctx(is_dark)
    figure = go.Figure()
    summary = summary or {}
    total = finite_number(summary.get('total_seconds'))
    if total:
        values = [finite_number(summary.get(key)) for key in ('new_seconds', 'same_day_rework_seconds')]
        if any(value is None for value in values):
            raise ValueError('Missing work composition')
        from math import isclose
        if not isclose(sum(values), total):
            raise ValueError('Work composition does not match total')
        figure.add_trace(go.Pie(labels=['Fresh work', 'Same-day rework'], values=values,
            hole=.68, sort=False, marker={'colors': ['#38bdf8', '#fbbf24']},
            textinfo='percent', textposition='inside', automargin=True,
            customdata=[format_video_seconds(value) for value in values],
            hovertemplate='%{label}<br>%{customdata} · %{percent}<extra></extra>'))
        label = format_video_seconds(total)
    else:
        label = 'No new or same-day work' if total == 0 and summary.get('available') else 'Daily data unavailable'
    figure.add_annotation(text=label, x=.5, y=.5, xref='paper', yref='paper', showarrow=False,
                          font={'size': 16})
    figure.update_layout(template=theme['template'], paper_bgcolor=theme['bg_color'],
        plot_bgcolor=theme['bg_color'], font={'color': theme['font_color'], 'family': 'Inter, sans-serif'},
        height=360, autosize=True, margin={'l': 12, 'r': 12, 't': 15, 'b': 90},
        legend={'orientation': 'h', 'y': -.15, 'yanchor': 'top', 'x': .5, 'xanchor': 'center', 'font': {'size': 11}},
        uirevision='individual-work-breakdown')
    return figure
