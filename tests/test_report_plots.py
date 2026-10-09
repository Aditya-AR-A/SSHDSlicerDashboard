"""Report renderers preserve prepared values, missing dates and shared styling."""
import json
import unittest

import pandas as pd
from dash.dash_table.Format import Format, Scheme
from plotly.utils import PlotlyJSONEncoder

from slicing_dashboard.plots.kpi_cards import _make_kpi_card, make_kpi_card
from slicing_dashboard.plots.tables import create_table
from slicing_dashboard.plots.theme import USER_COLORS, user_color
from slicing_dashboard.plots.work_trend_chart import (
    build_user_comparison_chart, build_work_composition_chart,
)


class ReportPlotTests(unittest.TestCase):
    def test_user_trend_preserves_verified_zero_and_missing_days(self):
        rows = [
            {"date": "2026-10-03", "user": "Riya", "total_seconds": 3661},
            {"date": "2026-10-04", "user": "Riya", "total_seconds": None},
            {"date": "2026-10-05", "user": "Riya", "total_seconds": 0},
            {"date": "2026-10-05", "user": "Komal", "total_seconds": 7200},
        ]
        figure = build_user_comparison_chart(rows, users=["Riya", "Komal"])
        riya, komal = figure.data
        self.assertEqual(list(riya.x), ["2026-10-03", "2026-10-04", "2026-10-05"])
        self.assertEqual(list(riya.y), [3661 / 3600, None, 0])
        self.assertEqual(list(komal.y), [None, None, 2])
        self.assertFalse(riya.connectgaps)
        self.assertEqual(riya.customdata[0][1], "01:01:01")
        self.assertEqual(riya.customdata[1][1], "Unavailable")
        self.assertEqual(riya.customdata[2][1], "00:00:00")
        self.assertIn("%{customdata[0]}", riya.hovertemplate)
        self.assertIn("%{x|", riya.hovertemplate)

    def test_user_colors_and_legend_survive_theme_and_data_refresh(self):
        rows = [{"date": "2026-10-05", "user": user, "total_seconds": 3600}
                for user in ("Riya", "Akash", "New Person")]
        dark = build_user_comparison_chart(rows, selected_users=["Riya"])
        light = build_user_comparison_chart(rows, is_dark=False, selected_users=["Riya"])
        self.assertEqual([trace.line.color for trace in dark.data], [trace.line.color for trace in light.data])
        self.assertEqual(dark.layout.uirevision, light.layout.uirevision)
        self.assertEqual(dark.layout.legend.uirevision, light.layout.legend.uirevision)
        self.assertEqual({trace.name: trace.visible for trace in dark.data},
                         {"Akash": "legendonly", "New Person": "legendonly", "Riya": True})
        self.assertEqual(user_color("Riya"), USER_COLORS["Riya"])
        self.assertNotEqual(user_color("New Person"), "#999")
        people = ("Aditya", "Akash", "Annotator", "Gurharleen", "Komal", "Pardeep",
                  "Priya", "Rajni", "Ranjeeta", "Riya", "Sanddep", "Sheetal")
        self.assertEqual(len({user_color(person) for person in people}), len(people))

    def test_unknown_history_is_an_empty_state_and_valid_zero_is_not(self):
        unavailable = build_user_comparison_chart([
            {"date": "2026-10-05", "user": "Riya", "total_seconds": None}])
        zero = build_user_comparison_chart([
            {"date": "2026-10-05", "user": "Riya", "total_seconds": 0}])
        self.assertIn("unavailable", unavailable.layout.annotations[0].text)
        self.assertEqual(len(zero.layout.annotations), 0)

    def test_composition_preserves_categories_without_double_counting_old_rework(self):
        rows = [
            {"date": "2026-10-04", "new_seconds": None, "same_day_rework_seconds": None,
             "old_rework_seconds": None, "total_seconds": None},
            {"date": "2026-10-05", "new_seconds": 3600, "same_day_rework_seconds": 1800,
             "old_rework_seconds": 900, "total_seconds": 5400},
        ]
        figure = build_work_composition_chart(rows)
        self.assertEqual([trace.name for trace in figure.data], ["Fresh Work", "Same-day Rework", "Old Rework (excluded)"])
        self.assertEqual([trace.y[0] for trace in figure.data], [None, None, None])
        self.assertEqual(sum(trace.y[1] for trace in figure.data if trace.type == 'bar'), 5400 / 3600)
        self.assertEqual(figure.data[2].customdata[1][0], "00:15:00")
        self.assertEqual(figure.layout.barmode, "stack")

    def test_report_kpi_has_no_invented_sparkline_and_distinguishes_missing_value(self):
        missing = make_kpi_card("Today's work", None)
        zero = make_kpi_card("Today's work", "00:00:00")
        missing_payload = json.dumps(missing, cls=PlotlyJSONEncoder, ensure_ascii=False)
        zero_payload = json.dumps(zero, cls=PlotlyJSONEncoder, ensure_ascii=False)
        self.assertIn("—", missing_payload)
        self.assertIn("00:00:00", zero_payload)
        self.assertNotIn('"type": "Graph"', missing_payload)
        with_trend = make_kpi_card("Today's work", "01:00:00", sparkline_data=[0, None, 3600])
        graph = with_trend.children.children.children[2]
        self.assertEqual(list(graph.figure.data[0].y), [0, None, 3600])
        self.assertFalse(graph.figure.data[0].connectgaps)
        self.assertEqual(graph.figure.data[0].line.shape, "linear")
        legacy = _make_kpi_card("Approved", "01:00", "bi bi-check", "10%", "#10B981", True, [1, 2, 3])
        self.assertEqual(legacy.children.children.children[2].figure.data[0].line.shape, "spline")

    def test_typed_table_columns_keep_numeric_sort_values_and_unknown_null(self):
        frame = pd.DataFrame({"User": ["Riya", "Komal"], "Video Hours": [12.25, None]})
        columns = [{"name": "Person", "id": "User"},
                   {"name": "Video hours", "id": "Video Hours", "type": "numeric",
                    "format": Format(precision=2, scheme=Scheme.fixed)}]
        table = create_table(frame, False, table_id="daily-report-table", columns=columns, page_size=10)
        self.assertEqual(table.id, "daily-report-table")
        self.assertEqual(table.columns, columns)
        self.assertEqual(table.data, [{"User": "Riya", "Video Hours": 12.25}, {"User": "Komal", "Video Hours": None}])
        self.assertEqual(table.style_table["overflowX"], "auto")
        self.assertEqual(table.page_size, 10)

    def test_numeric_table_tooltips_keep_exact_clock_values_without_replacing_sort_data(self):
        frame = pd.DataFrame({"User": ["Riya", "Komal"], "Video Hours": [3661 / 3600, 0]})
        tooltips = [{"Video Hours": {"value": "01:01:01", "type": "text"}},
                    {"Video Hours": {"value": "00:00:00", "type": "text"}}]
        table = create_table(frame, True, tooltip_data=tooltips)
        self.assertEqual(table.tooltip_data, tooltips)
        self.assertIsNone(table.tooltip_duration)
        self.assertEqual(table.data[0]["Video Hours"], 3661 / 3600)
        self.assertEqual(table.data[1]["Video Hours"], 0)

    def test_twelve_user_comparison_stays_compact(self):
        rows = [{"date": f"2026-09-{day:02}", "user": f"Person {person}", "total_seconds": person * 3600}
                for person in range(12) for day in range(1, 31)]
        figure = build_user_comparison_chart(rows)
        self.assertEqual(len(figure.data), 12)
        self.assertLess(len(figure.to_json().encode("utf-8")), 55_000)


if __name__ == "__main__":
    unittest.main()
