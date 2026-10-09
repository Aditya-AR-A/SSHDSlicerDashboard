"""Daily report charts rendered from prepared, coverage-aware second totals."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite

import plotly.graph_objects as go

from slicing_dashboard.plots.theme import theme_ctx, user_color
from slicing_dashboard.plots.guardrails import safe_chart, dated_records, add_bar_total_labels


def _seconds(value) -> float | None:
    """Keep unknown values as gaps instead of silently displaying zero work."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def _duration_label(seconds) -> str:
    value = _seconds(seconds)
    if value is None:
        return "Unavailable"
    hours, remainder = divmod(int(round(value)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _layout(figure, title, is_dark, revision, *, composition=False):
    theme = theme_ctx(is_dark)
    figure.update_layout(
        title={"text": title, "font": {"size": 14}},
        template=theme["template"],
        paper_bgcolor=theme["bg_color"], plot_bgcolor=theme["bg_color"],
        font={"family": "Inter, sans-serif", "size": 12, "color": theme["font_color"]},
        hoverlabel={"bgcolor": theme["hover_bg"], "font": {"color": theme["hover_fg"]}},
        height=380 if composition else 420, autosize=True,
        margin={"l": 55, "r": 20, "t": 55, "b": 85 if composition else 170},
        xaxis={"title": {"text": "Date", "standoff": 5, "font": {"size": 11}},
               "type": "date", "tickformat": "%d %b", "nticks": 7,
               "showgrid": False, "automargin": True},
        yaxis={"title": "Submitted video hours", "rangemode": "tozero", "automargin": True,
               "gridcolor": "rgba(128,128,128,.2)"},
        hovermode="x unified",
        legend={"orientation": "h", "x": 0, "xanchor": "left", "y": -.2,
                "yanchor": "top", "font": {"size": 10}, "entrywidth": 60,
                "itemclick": "toggle", "itemdoubleclick": "toggleothers"},
        uirevision=revision,
    )
    return figure


@safe_chart(height=420)
def build_user_comparison_chart(
    rows: Iterable[Mapping],
    is_dark: bool = True,
    users: list[str] | None = None,
    selected_users: list[str] | None = None,
    title: str = "Last 30 days · Submitted video duration",
) -> go.Figure:
    """Plot one daily output line per person, with missing days left unconnected.

    Rows contain ``date``, ``user`` and ``total_seconds``. Callers prepare all
    report dates and verified zero days; absent or null values stay unavailable.
    Other work-type fields may be present but are not transformed here.
    """
    records = dated_records(rows, identity=('user',))
    dates = sorted({str(row["date"]) for row in records if row.get("date")})
    people = list(dict.fromkeys(users)) if users is not None else sorted({
        str(row["user"]) for row in records if row.get("user")
    })
    prepared = {(str(row["date"]), str(row["user"])): row
                for row in records if row.get("date") and row.get("user")}
    selection = set(people if selected_users is None else selected_users)
    revision = f"daily-comparison:{dates[0] if dates else ''}:{dates[-1] if dates else ''}"
    figure = go.Figure()
    has_values = False
    for person in people:
        values = [_seconds(prepared.get((date, person), {}).get("total_seconds")) for date in dates]
        has_values = has_values or any(value is not None for value in values)
        figure.add_trace(go.Scatter(
            name=person, x=dates,
            y=[value / 3600 if value is not None else None for value in values],
            customdata=[[person, _duration_label(value)] for value in values],
            mode="lines+markers", connectgaps=False,
            line={"color": user_color(person), "width": 2}, marker={"size": 4},
            visible=True if person in selection else "legendonly",
            hovertemplate=("%{x|%d %b %Y}<br><b>%{customdata[0]}</b>"
                           "<br>Video output: %{customdata[1]} (%{y:.2f}h)<extra></extra>"),
        ))
    _layout(figure, title, is_dark, revision)
    figure.update_layout(legend={"uirevision": revision + ":" + "|".join(sorted(selection))})
    if not has_values:
        figure.add_annotation(text="Daily history is unavailable for this period.",
                              x=.5, y=.5, xref="paper", yref="paper", showarrow=False)
    return figure


@safe_chart(height=380)
def build_work_composition_chart(
    rows: Iterable[Mapping],
    is_dark: bool = True,
    title: str = "Daily fresh work and rework",
) -> go.Figure:
    """Plot prepared team work-type totals; this builder does not aggregate users."""
    records = dated_records(rows)
    dates = [str(row["date"]) for row in records]
    figure = go.Figure()
    categories = [
        ("Fresh Work", "new_seconds", "#38bdf8" if is_dark else "#0284c7"),
        ("Same-day Rework", "same_day_rework_seconds", "#fbbf24" if is_dark else "#b45309"),
        ("Old Rework", "old_rework_seconds", "#f97316" if is_dark else "#c2410c"),
    ]
    has_values = False
    for label, field, color in categories:
        values = [_seconds(row.get(field)) for row in records]
        has_values = has_values or any(value is not None for value in values)
        trace_args = dict(
            name=label, x=dates,
            y=[value / 3600 if value is not None else None for value in values],
            marker_color=color,
            customdata=[[_duration_label(value), _duration_label(row.get("total_seconds"))]
                        for value, row in zip(values, records)],
            hovertemplate=(f"%{{x|%d %b %Y}}<br>{label}: %{{customdata[0]}}"
                           "<br>Total work (new + same-day): %{customdata[1]}<extra></extra>"),
        )
        if field == 'old_rework_seconds':
            trace_args['name'] = 'Old Rework (excluded)'
            figure.add_trace(go.Scatter(**trace_args, mode='lines+markers', connectgaps=False,
                                       line={'color': color, 'width': 2}))
        else:
            figure.add_trace(go.Bar(**trace_args))
    revision = f"daily-composition:{dates[0] if dates else ''}:{dates[-1] if dates else ''}"
    _layout(figure, title, is_dark, revision, composition=True)
    figure.update_layout(barmode="stack", bargap=.2)
    if not has_values:
        figure.add_annotation(text="Work-type history is unavailable for this period.",
                              x=.5, y=.5, xref="paper", yref="paper", showarrow=False)
    return add_bar_total_labels(figure)


@safe_chart(height=360)
def build_individual_work_chart(rows, user, is_dark=True):
    """One total line, optional work-type lines and a coverage-aware rolling mean."""
    records = dated_records(rows)
    figure = go.Figure()
    series = [('Total', 'total_seconds', user_color(user), True),
              ('Fresh', 'new_seconds', '#38bdf8', 'legendonly'),
              ('Same-day', 'same_day_rework_seconds', '#fbbf24', 'legendonly'),
              ('Old (excluded)', 'old_rework_seconds', '#f97316', 'legendonly')]
    if any(row.get('rolling_mean_seconds') is not None for row in records):
        series.append(('7-day mean', 'rolling_mean_seconds', '#94a3b8', 'legendonly'))
    for name, key, color, visible in series:
        values = [_seconds(row.get(key)) for row in records]
        figure.add_trace(go.Scatter(name=name, x=[row['date'] for row in records],
            y=[value / 3600 if value is not None else None for value in values],
            customdata=[_duration_label(value) for value in values], mode='lines+markers',
            connectgaps=False, visible=visible, line={'color': color, 'width': 2},
            hovertemplate='%{x|%d %b %Y}<br>%{customdata}<extra>%{fullData.name}</extra>'))
    revision = f"user-work:{user}:{records[-1]['date'] if records else ''}"
    _layout(figure, 'Recorded daily output · last 30 days', is_dark, revision)
    figure.update_layout(title=None, height=360, margin={'l': 48, 'r': 12, 't': 15, 'b': 110},
                         legend={'y': -.3, 'font': {'size': 10}, 'entrywidth': 65})
    if not any(row.get('total_seconds') is not None for row in records):
        figure.add_annotation(text='No recorded daily history for this period.', x=.5, y=.5,
                              xref='paper', yref='paper', showarrow=False)
    return figure
