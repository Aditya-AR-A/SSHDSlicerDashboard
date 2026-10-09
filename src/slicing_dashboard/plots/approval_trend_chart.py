"""Presentation of prepared daily throughput; gaps are never filled with zeros."""
import plotly.graph_objects as go

from slicing_dashboard.plots.work_trend_chart import _layout, _duration_label, _seconds
from slicing_dashboard.plots.guardrails import safe_chart, dated_records
from slicing_dashboard.reporting.approval_reports import APPROVAL_STAGES


@safe_chart(height=420)
def build_approval_trend_chart(report, is_dark=True):
    rows = dated_records((report or {}).get('rows', []))
    dates = [row['date'] for row in rows]
    figure = go.Figure()
    series = [('total_seconds', 'Total Work Done'), *APPROVAL_STAGES]
    colors = ['#38bdf8', '#a78bfa', '#fbbf24', '#34d399']
    if any('completed_seconds' in row for row in rows):
        series.append(('completed_seconds', 'Completed Video (source date)'))
        colors.append('#fb7185')
    for (field, label), color in zip(series, colors):
        values = [_seconds(row.get(field)) for row in rows]
        available = any(value is not None for value in values)
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        figure.add_trace(go.Scatter(
            name=label if available else label + ' (unavailable)', x=dates,
            y=[value / 3600 if value is not None else None for value in values],
            customdata=[_duration_label(value) for value in values],
            mode='lines+markers', connectgaps=False,
            fill='tozeroy', fillcolor=f'rgba({rgb[0]},{rgb[1]},{rgb[2]},0.16)',
            line={'color': color, 'width': 2}, marker={'size': 4},
            hovertemplate=('%{x|%d %b %Y}<br>%{customdata}'
                           + ('<br>Source-date current status · Asia/Shanghai' if field == 'completed_seconds'
                              else '<br>Batch-duration estimate' if field != 'total_seconds' else '')
                           + '<extra>%{fullData.name}</extra>'),
        ))
    revision = f"approval-trend:{report.get('range_start')}:{report.get('range_end')}"
    _layout(figure, 'Daily work and approvals · last 30 days', is_dark, revision)
    figure.update_layout(margin={'b': 150}, yaxis_title='Video hours',
                         legend={'entrywidth': 210})
    if not any(row.get('total_seconds') is not None for row in rows):
        figure.add_annotation(text='No recorded submissions for this period.',
                              x=.5, y=.5, xref='paper', yref='paper', showarrow=False)
    return figure
