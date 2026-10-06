"""Today-only composition; settlement and overall amounts are separate scopes."""
import plotly.graph_objects as go
from slicing_dashboard.plots.theme import theme_ctx
from slicing_dashboard.processing.daily_work import format_video_seconds


def build_work_breakdown_donut(summary, is_dark=True):
    theme = theme_ctx(is_dark)
    figure = go.Figure()
    total = summary.get('total_seconds')
    if total:
        values = [summary[key] for key in ('new_seconds', 'same_day_rework_seconds', 'old_rework_seconds')]
        figure.add_trace(go.Pie(labels=['Fresh work', 'Same-day rework', 'Old rework'], values=values,
            hole=.68, sort=False, marker={'colors': ['#38bdf8', '#fbbf24', '#f97316']},
            textinfo='percent', customdata=[format_video_seconds(value) for value in values],
            hovertemplate='%{label}<br>%{customdata} · %{percent}<extra></extra>'))
        label = format_video_seconds(total)
    else:
        label = 'No submitted work' if summary.get('available') else 'Daily data unavailable'
    figure.add_annotation(text=label, x=.5, y=.5, xref='paper', yref='paper', showarrow=False,
                          font={'size': 16})
    figure.update_layout(template=theme['template'], paper_bgcolor=theme['bg_color'],
        plot_bgcolor=theme['bg_color'], font={'color': theme['font_color'], 'family': 'Inter, sans-serif'},
        height=340, margin={'l': 15, 'r': 15, 't': 20, 'b': 75},
        legend={'orientation': 'h', 'y': -.1, 'x': .5, 'xanchor': 'center'},
        uirevision='individual-work-breakdown')
    return figure
