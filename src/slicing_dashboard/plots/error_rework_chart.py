"""Daily video output, separated into fresh, same-day and older rework."""
import plotly.graph_objects as go
from slicing_dashboard.plots.theme import CHART_HEIGHT, theme_ctx
from slicing_dashboard.processing.daily_work import format_video_seconds as format_seconds


def build_error_rework_chart(active_work_df, chart_date_label, is_dark):
    theme = theme_ctx(is_dark)
    figure = go.Figure()
    maximum = 1
    if active_work_df.empty:
        figure.add_annotation(text=f"No submissions recorded ({chart_date_label})", showarrow=False,
                              font={"size": 14, "color": theme["font_color"]})
    else:
        rows = active_work_df.sort_values("Total Work Duration", ascending=False)
        buckets = [("Fresh Work", "New Work Duration", "#38bdf8" if is_dark else "#0284c7")]
        if "Same-day Rework Duration" in rows:
            buckets += [("Same-day Rework", "Same-day Rework Duration", "#fbbf24" if is_dark else "#b45309"),
                        ("Old Rework", "Old Rework Duration", "#f97316" if is_dark else "#c2410c")]
        else:
            buckets += [("Rework", "Rework Duration", "#f97316")]
        total = rows["Total Work Duration"].apply(format_seconds)
        ids = rows["IDs"].tolist() if "IDs" in rows else [""] * len(rows)
        for label, column, color in buckets:
            durations = rows[column].apply(format_seconds)
            figure.add_trace(go.Bar(name=label, x=rows["User"], y=rows[column] / 3600, text=durations,
                                   marker_color=color, customdata=list(zip(durations, total, ids)),
                                   hovertemplate=f"<b>%{{x}}</b><br>{label}: %{{customdata[0]}}<br>Total video output: %{{customdata[1]}}<br>Accounts: %{{customdata[2]}}<extra></extra>"))
        maximum = max(1, rows["Total Work Duration"].max() / 3600 * 1.2)
    figure.update_layout(
        title=f"{chart_date_label} · Submitted Video Duration", barmode="stack",
        template=theme["template"], plot_bgcolor=theme["bg_color"], paper_bgcolor=theme["bg_color"],
        font={"family": "Inter, sans-serif", "size": 12, "color": theme["font_color"]},
        height=CHART_HEIGHT, margin={"l": 40, "r": 15, "t": 55, "b": 35},
        clickmode="event+select", legend={"orientation": "h", "y": 1.04, "x": 0},
        yaxis={"title": "Video hours", "range": [0, maximum], "gridcolor": "rgba(128,128,128,.2)"},
        xaxis={"showgrid": False, "automargin": True},
    )
    figure.update_traces(textposition="auto", textfont={"size": 10})
    return figure
