"""Daily team reporting built from shared verified submission data."""
from datetime import date
from dash import dcc, html
from dash.dash_table.Format import Format, Scheme
import dash_bootstrap_components as dbc

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.pages.components import report_section
from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart
from slicing_dashboard.plots.kpi_cards import make_kpi_card
from slicing_dashboard.plots.tables import create_table
from slicing_dashboard.plots.work_trend_chart import build_user_comparison_chart, build_work_composition_chart
from slicing_dashboard.processing.daily_work import format_video_seconds
from slicing_dashboard.reporting.dashboard_reports import (
    summarize_daily, prepare_daily_table, prepare_daily_work_chart_rows,
    prepare_user_comparison_rows, prepare_team_composition_rows, day_over_day,
)


def layout_daily_report(today):
    return html.Div([
        html.Div([
            html.Div([
                html.H1("Daily Report", className="h3 mb-1"),
                html.P("Daily team output, rework, and individual trends.", className="text-secondary mb-0"),
            ]),
            html.Div([
                html.Label("Report date", htmlFor="daily-report-date", className="small fw-semibold"),
                dbc.Input(id="daily-report-date", type="date", value=today, max=today,
                          className="date-input-custom", size="sm"),
                dbc.Button("Today", id="daily-report-today", size="sm", color="outline-primary"),
                html.Span("India time", className="text-secondary small"),
            ], className="report-controls"),
        ], className="report-header"),
        dcc.Store(id="daily-date-follows-today", data=True),
        dcc.Loading([
            dcc.Store(id="daily-report-store"),
            html.Div(id="daily-report-content", children=html.P("Loading daily report…", className="text-secondary")),
        ], type="circle"),
    ], className="report-page")


def _available(day):
    return bool((day or {}).get("metadata", {}).get("available", False))


def _day_label(value, report_date, previous=False):
    if report_date == today_iso():
        return "Yesterday" if previous else "Today"
    try:
        return date.fromisoformat(value).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(value or "Selected day")


def _day_notice(day):
    metadata = (day or {}).get("metadata", {})
    if not metadata.get("available"):
        message = "Daily submissions could not be loaded. Retry Refresh." if metadata.get("error") else "No verified history is available for this date."
        return dbc.Alert(message, color="warning", className="py-2 small")
    if metadata.get("is_snapshot") or metadata.get("error"):
        return dbc.Alert(f"Showing saved submissions from {metadata.get('captured_at', 'an earlier capture')}. Refresh could not be verified.",
                         color="warning", className="py-2 small")
    if metadata.get("persistence_error"):
        return dbc.Alert("This capture is available in memory, but could not be saved. Refresh to retry saving daily history.",
                         color="warning", className="py-2 small")
    capture = metadata.get("captured_at")
    note = "Observed submissions; video duration, excluding approvals and pending rework. Activity outside recorded captures may be unavailable."
    if capture:
        note += f" Captured {capture}."
    return html.P(note, className="text-secondary small mb-2")


def _value(summary, field):
    value = summary.get(field)
    return "—" if value is None else format_video_seconds(value)


def _day_table(day, is_dark, table_id):
    if not _available(day):
        return _day_notice(day)
    rows = prepare_daily_table(day, numeric_durations=True)
    if not rows:
        return html.Div([_day_notice(day), html.P("No submissions recorded for this day.", className="text-secondary")])
    import pandas as pd
    duration_names = {"Total Duration": "Total Work (h)", "New Videos (First Time)": "New Work (h)",
                      "Same-day Rework": "Same-day Rework (h)", "Old Rework": "Old Rework (h)"}
    columns = [{"name": duration_names.get(column, column), "id": column,
                **({"type": "numeric", "format": Format(precision=3, scheme=Scheme.fixed)} if column in duration_names else
                   {"type": "numeric"} if column.endswith("Tasks") else {})}
               for column in rows[0]]
    tooltip = [{column: {"value": value, "type": "text"} for column, value in clock_row.items() if column in duration_names}
               for clock_row in prepare_daily_table(day)]
    return html.Div([
        _day_notice(day),
        html.Div(create_table(pd.DataFrame(rows), is_dark, table_id=table_id, columns=columns,
                              tooltip_data=tooltip, max_height="450px"), className="report-table-scroll"),
    ])


def build_daily_report_content(data, is_dark=True):
    """Render a prepared report; no fetching occurs on theme/chart changes."""
    if not data:
        return html.P("Loading daily report…", className="text-secondary")
    if data.get("validation_error"):
        return dbc.Alert(data["validation_error"], color="warning")
    today = data.get("today", {})
    yesterday = data.get("yesterday", {})
    summary, previous = summarize_daily(today), summarize_daily(yesterday)
    report_date = data.get("report_date")
    current_label = _day_label(report_date, report_date)
    previous_label = _day_label(data.get("previous_date"), report_date, previous=True)
    comparison_change = day_over_day(today, yesterday)
    if comparison_change["difference_seconds"] is None:
        change = "Previous-day comparison unavailable"
    else:
        difference = comparison_change["difference_seconds"]
        change = f"{difference / 3600:+.2f}h vs recorded previous day"
        if comparison_change["percentage"] is not None:
            change += f" ({comparison_change['percentage']:+.1f}%)"
        if report_date == today_iso():
            change = "Today so far: " + change
    kpis = [
        make_kpi_card(f"{current_label}'s Total Work" if current_label == "Today" else "Selected-day Total Work",
                      _value(summary, "total_seconds"), badge_text=change, is_dark=is_dark),
        make_kpi_card("New Work", _value(summary, "new_seconds"), badge_text="Fresh submissions", is_dark=is_dark, badge_color="#38bdf8"),
        make_kpi_card("Same-day Rework", _value(summary, "same_day_rework_seconds"), badge_text="Returned work from the same day", is_dark=is_dark, badge_color="#fbbf24"),
        make_kpi_card("Old Rework", _value(summary, "old_rework_seconds"), badge_text="Rework from a previous day", is_dark=is_dark, badge_color="#f97316"),
        make_kpi_card("Yesterday's Total Work" if previous_label == "Yesterday" else "Previous-day Total Work",
                      _value(previous, "total_seconds"), badge_text=previous_label, is_dark=is_dark, badge_color="#a78bfa"),
        make_kpi_card("Active Users", "—" if summary.get("active_users") is None else str(summary["active_users"]),
                      badge_text="People with submitted tasks", icon_class="bi bi-people", is_dark=is_dark, badge_color="#34d399"),
    ]
    work = build_error_rework_chart(prepare_daily_work_chart_rows(today), current_label, is_dark)
    if not _available(today):
        work.layout.annotations[0].text = "Daily submissions unavailable for this date"
    if today.get("metadata", {}).get("is_snapshot"):
        work.update_layout(title=f"{current_label} · Saved Submitted Video Duration")
    history = data.get("history", [])
    users = data.get("users", [])
    comparison_rows = prepare_user_comparison_rows(history, users)
    comparison = build_user_comparison_chart(comparison_rows, is_dark=is_dark, users=users)
    composition = build_work_composition_chart(prepare_team_composition_rows(history), is_dark=is_dark)
    available_days = [day for day in history if _available(day)]
    coverage = f"{len(available_days)} of {len(history) or 30} days recorded"
    if available_days:
        coverage += f" · Available from {min(day['date'] for day in available_days)}"
    history_note = "Missing dates appear as gaps. History grows from recorded daily submissions; earlier work is not backfilled."
    return html.Div([
        html.Div(kpis, className="report-kpi-grid"),
        html.Div(_day_notice(today), className="report-status", **{"aria-live": "polite"}),
        report_section(f"{current_label}'s Work" if current_label == "Today" else f"Work · {current_label}",
                       dcc.Graph(id="daily-work-chart", figure=work, config={"displayModeBar": False, "responsive": True})),
        report_section("30-day Team Comparison", html.Div([
            html.P(coverage, className="fw-semibold small mb-1"),
            html.P(history_note, className="text-secondary small"),
            dcc.Graph(id="daily-comparison-chart", figure=comparison, className="report-comparison-chart",
                      config={"displayModeBar": False, "responsive": True}),
        ]), "Click a name in the legend to show or hide that person. Double-click to isolate a line."),
        report_section("New Work and Rework Trend",
                       dcc.Graph(id="daily-composition-chart", figure=composition, config={"displayModeBar": False, "responsive": True}),
                       "Team totals by work type, using the same recorded daily history."),
        report_section(f"{current_label}'s Work Table" if current_label == "Today" else f"Work Table · {current_label}",
                       _day_table(today, is_dark, "daily-today-table")),
        report_section("Yesterday's Work Table" if previous_label == "Yesterday" else f"Previous-day Work Table · {previous_label}",
                       _day_table(yesterday, is_dark, "daily-yesterday-table")),
    ], className="report-content")
