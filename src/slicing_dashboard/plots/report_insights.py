"""Report comparisons and calendar views built only from retained daily records."""
from datetime import date, timedelta

import plotly.graph_objects as go

from slicing_dashboard.plots.theme import theme_ctx
from slicing_dashboard.plots.guardrails import safe_chart, pad_hour_axis, hour_total_label
from slicing_dashboard.processing.daily_work import format_video_seconds
from slicing_dashboard.reporting.dashboard_reports import summarize_daily


def _layout(figure, is_dark, height):
    theme = theme_ctx(is_dark)
    figure.update_layout(template=theme['template'], paper_bgcolor=theme['bg_color'],
                         plot_bgcolor=theme['bg_color'], font_color=theme['font_color'],
                         height=height, autosize=True, margin=dict(l=85, r=20, t=25, b=65),
                         legend=dict(orientation='h', y=-.18, x=0),
                         hoverlabel=dict(bgcolor=theme['hover_bg'], font_color=theme['hover_fg']))


@safe_chart(height=360)
def build_day_comparison(today, previous, users, is_dark=True):
    """Paired bars preserve unknown days and captured zero-work people."""
    figure = go.Figure()
    ordered = sorted(set(users or []), key=lambda user: (summarize_daily(today, [user])['total_seconds'] or 0, user))
    for label, day, color in [('Selected day', today, '#38bdf8'), ('Previous day', previous, '#a78bfa')]:
        seconds = [summarize_daily(day, [user])['total_seconds'] for user in ordered]
        figure.add_trace(go.Bar(name=label, y=ordered,
            x=[value / 3600 if value is not None else None for value in seconds],
            orientation='h', marker_color=color,
            text=[hour_total_label(value / 3600) if value is not None else '' for value in seconds],
            textposition='outside', textfont={'size': 10},
            customdata=[[day.get('date', ''), format_video_seconds(value) if value is not None else 'Unavailable']
                        for value in seconds],
            hovertemplate='%{y}<br>%{customdata[0]}<br>Submitted video: %{customdata[1]}<extra>%{fullData.name}</extra>'))
    _layout(figure, is_dark, max(360, 100 + len(ordered) * 34))
    figure.update_layout(barmode='group', bargap=.25, uirevision='daily-day-comparison')
    figure.update_xaxes(title='Submitted video hours', rangemode='tozero',
                       gridcolor='rgba(148,163,184,.16)')
    figure.update_yaxes(automargin=True)
    missing = [label for label, day in [('Selected day', today), ('Previous day', previous)]
               if not day.get('metadata', {}).get('available')]
    if missing:
        figure.add_annotation(text=' / '.join(missing) + ' unavailable', x=.5, y=1.07,
                              xref='paper', yref='paper', showarrow=False)
    figure.update_layout(margin={'r': 60})
    return pad_hour_axis(figure, 'h')


@safe_chart(height=360)
def build_activity_calendar(days, user, end_date, is_dark=True):
    """Ninety calendar days, with distinct recorded zero and unknown cells."""
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=89)
    monday = start - timedelta(days=start.weekday())
    weeks = (end - monday).days // 7 + 1
    values = [[None] * weeks for _ in range(7)]
    unknown = [[None] * weeks for _ in range(7)]
    labels = [[''] * weeks for _ in range(7)]
    captured = 0
    for offset in range(90):
        current = start + timedelta(days=offset)
        column, weekday = (current - monday).days // 7, current.weekday()
        key = current.isoformat()
        seconds = summarize_daily(days.get(key, {}), [user])['total_seconds']
        if seconds is None:
            unknown[weekday][column] = 1
            labels[weekday][column] = f'{key}<br>No recorded capture'
        else:
            captured += 1
            values[weekday][column] = seconds / 3600
            labels[weekday][column] = f'{key}<br>Submitted video: {format_video_seconds(seconds)}'
    columns = [(monday + timedelta(days=week * 7)).isoformat() for week in range(weeks)]
    weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    figure = go.Figure()
    figure.add_trace(go.Heatmap(x=columns, y=weekdays, z=unknown, text=labels,
        colorscale=[[0, '#334155' if is_dark else '#e2e8f0'], [1, '#334155' if is_dark else '#e2e8f0']],
        showscale=False, zmin=0, zmax=1, xgap=4, ygap=4, hoverongaps=False,
        hovertemplate='%{text}<extra>Unavailable</extra>'))
    figure.add_trace(go.Heatmap(x=columns, y=weekdays, z=values, text=labels,
        colorscale=[[0, '#172c3b' if is_dark else '#e0f2fe'], [.3, '#38bdf8'], [1, '#0284c7']],
        zmin=0, zmax=max(1, max((value for row in values for value in row if value is not None), default=0)),
        colorbar=dict(title='Video hours', orientation='h', thickness=8, len=.8,
                      x=.5, xanchor='center', y=-.2, yanchor='top', tickfont={'size': 10}), xgap=4, ygap=4,
        hoverongaps=False, hovertemplate='%{text}<extra></extra>'))
    _layout(figure, is_dark, 360)
    figure.update_layout(margin=dict(l=38, r=12, t=15, b=85),
                         uirevision=f'activity:{user}:{end_date}')
    figure.update_yaxes(autorange='reversed', showgrid=False)
    figure.update_xaxes(type='category', tickvals=columns[::3], tickangle=0, tickfont={'size': 10},
                       ticktext=[date.fromisoformat(value).strftime('%d %b') for value in columns[::3]],
                       showgrid=False)
    return figure, captured
