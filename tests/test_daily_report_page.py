"""Daily Report integration without application startup, a database, or live APIs."""
import ast
from contextlib import nullcontext
from datetime import date, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import dash
from dash.development.base_component import Component
from plotly.utils import PlotlyJSONEncoder


APP_PATH = Path(__file__).parents[1] / "src/slicing_dashboard/app.py"


def application_namespace():
    """Keep real layout/callback registration and substitute only the manager."""
    manager = MagicMock()
    manager._snapshot_payload = {}
    manager.user_mapping = {"SSHD-Riya": "Riya", "SSHD-Priya": "Priya"}
    manager.get_available_periods.return_value = [{
        "start_date": "2026-10-01", "end_date": "2026-10-06", "is_current": True,
        "value": "2026-10-01|current", "label": "Current period",
    }]
    manager.get_server_status.return_value = {"is_live": True, "is_using_snapshot": False}
    manager.refresh_scope.side_effect = lambda: nullcontext()
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    replacements = 0
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "dm" for target in node.targets
        ):
            node.value = ast.Name(id="_test_manager", ctx=ast.Load())
            replacements += 1
    if replacements != 1:
        raise AssertionError("Application startup should create exactly one shared data manager")
    ast.fix_missing_locations(tree)
    namespace = {"__name__": __name__, "__file__": str(APP_PATH), "_test_manager": manager}
    with patch("slicing_dashboard.data_manager.DataManager", side_effect=AssertionError("Live startup")):
        exec(compile(tree, str(APP_PATH), "exec"), namespace)
    return namespace, manager


def components(value):
    if isinstance(value, Component):
        yield value
        yield from components(getattr(value, "children", None))
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from components(child)


def payload_text(value):
    return json.dumps(value, cls=PlotlyJSONEncoder)


def report_fixture(available=True, rows=None, error=None, stale=False):
    end = date(2026, 10, 6)
    history = []
    for offset in range(29, -1, -1):
        day = (end - timedelta(days=offset)).isoformat()
        is_today = offset == 0
        metadata = {
            "target_date": day, "timezone": "Asia/Kolkata", "available": available if is_today else False,
            "coverage": "observed" if is_today and available else "unavailable", "is_snapshot": stale if is_today else False,
        }
        if is_today:
            metadata["captured_at"] = "2026-10-06T16:00:00+05:30"
            if error:
                metadata["error"] = error
        history.append({"date": day, "rows": (rows or []) if is_today else [], "metadata": metadata})
    return {"report_date": end.isoformat(), "previous_date": "2026-10-05",
            "range_start": history[0]["date"], "range_end": end.isoformat(),
            "users": ["Priya", "Riya"], "today": history[-1], "yesterday": history[-2], "history": history}


class DailyReportRoutingTests(unittest.TestCase):
    def test_phase_one_navigation_and_pages_are_mounted(self):
        namespace, manager = application_namespace()
        by_id = {getattr(component, "id", None): component for component in components(namespace["app"].layout)}
        for identity in ("report-location", "dashboard-page", "daily-report-page", "report-not-found", "daily-report-store"):
            self.assertIn(identity, by_id)
        self.assertEqual(by_id["nav-dashboard"].href, "/")
        self.assertEqual(by_id["nav-daily-report"].href, "/reports/daily")
        links = [getattr(component, "href", None) for component in components(namespace["app"].layout)]
        self.assertNotIn("/reports/users", links)
        manager.get_daily_report_data.assert_not_called()
        manager.fetch_dashboard_data.assert_not_called()

    def test_daily_and_unknown_routes_hide_dashboard(self):
        namespace, _ = application_namespace()
        daily = namespace["route_pages"]("/reports/daily")
        self.assertEqual(daily[0].get("display"), "none")
        self.assertNotEqual(daily[1].get("display"), "none")
        self.assertEqual(daily[2].get("display"), "none")
        unknown = namespace["route_pages"]("/reports/users")
        self.assertEqual(unknown[0].get("display"), "none")
        self.assertEqual(unknown[1].get("display"), "none")
        self.assertNotEqual(unknown[2].get("display"), "none")

    def test_hidden_dashboard_never_fetches_its_sources(self):
        namespace, manager = application_namespace()
        result = namespace["update_dashboard"](
            1, 1, "2026-10-01", "2026-10-06", None, 0, "tab-settlement", None,
            ["Priya", "Riya"], "/reports/daily",
        )
        self.assertEqual(len(result), 9)
        self.assertTrue(all(value is dash.no_update for value in result))
        for name in ("fetch_dashboard_data", "get_summary_kpis", "get_todays_work_df", "get_cumulative_df",
                     "get_batch_rework_ratio_df", "get_detailed_pending_assigned_df"):
            getattr(manager, name).assert_not_called()

    def test_report_loading_is_route_scoped_and_theme_has_no_fetch_input(self):
        namespace, manager = application_namespace()
        with self.assertRaises(dash.exceptions.PreventUpdate):
            namespace["load_daily_report"]("/", 1, 1, "2026-10-06")
        manager.get_daily_report_data.assert_not_called()
        data = report_fixture()
        manager.get_daily_report_data.return_value = data
        context = MagicMock(triggered=[{"prop_id": "refresh-btn.n_clicks"}])
        with patch("dash.callback_context", context):
            self.assertEqual(namespace["load_daily_report"]("/reports/daily", 1, 1, "2026-10-06"), data)
        manager.get_daily_report_data.assert_called_once()
        self.assertEqual(manager.get_daily_report_data.call_args.kwargs.get("force_refresh"), True)
        callback = next(value for key, value in namespace["app"].callback_map.items()
                        if "daily-report-store.data" in key)
        self.assertNotIn("theme-toggle", {entry["id"] for entry in callback["inputs"]})

    def test_report_rendering_and_theme_changes_are_pure(self):
        namespace, manager = application_namespace()
        data = report_fixture()
        for clicks in (0, 1):
            result = namespace["render_daily_report"](data, clicks, "/reports/daily")
            self.assertEqual(len(result), 3)
            self.assertIsInstance(result[0], Component)
        manager.get_daily_report_data.assert_not_called()
        manager.get_todays_work_df.assert_not_called()
        with self.assertRaises(dash.exceptions.PreventUpdate):
            namespace["render_daily_report"](data, 0, "/")

    def test_registered_dashboard_dispatch_preserves_input_and_state_order(self):
        namespace, _ = application_namespace()
        namespace["update_dashboard"] = MagicMock(return_value="dashboard result")
        callback = next(value for key, value in namespace["app"].callback_map.items()
                        if "kpi-cards.children" in key)
        dispatch = callback["callback"].__wrapped__
        selected_users = ["Riya"]
        result = dispatch(0, 1, "2026-10-01", "2026-10-06", None, 0,
                          None, "/reports/daily", selected_users)
        self.assertEqual(result, "dashboard result")
        namespace["update_dashboard"].assert_called_once_with(
            0, 1, "2026-10-01", "2026-10-06", None, 0, None, None,
            selected_users, "/reports/daily",
        )

    def test_invalid_report_date_returns_a_visible_validation_state(self):
        namespace, manager = application_namespace()
        manager.get_daily_report_data.side_effect = ValueError("Invalid report date")
        context = MagicMock(triggered=[{"prop_id": "daily-report-date.value"}])
        with patch("dash.callback_context", context):
            payload = namespace["load_daily_report"]("/reports/daily", 0, 0, "not-a-date")
        self.assertIn("validation_error", payload)
        content, _, _ = namespace["render_daily_report"](payload, 0, "/reports/daily")
        self.assertIn(payload["validation_error"], payload_text(content))
        self.assertFalse(any(getattr(component, "id", None) == "daily-work-chart"
                             for component in components(content)))

    def test_midnight_advances_followed_today_and_preserves_selected_history(self):
        namespace, _ = application_namespace()
        namespace["today_iso"] = MagicMock(return_value="2026-10-07")
        interval = MagicMock(triggered=[{"prop_id": "auto-refresh-interval.n_intervals"}])
        with patch("dash.callback_context", interval):
            self.assertEqual(namespace["follow_daily_date"](0, 1, "2026-10-06", True),
                             ("2026-10-07", True, "2026-10-07"))
            pinned = namespace["follow_daily_date"](0, 1, "2026-10-05", False)
        self.assertIs(pinned[0], dash.no_update)
        self.assertEqual(pinned[1:], (False, "2026-10-07"))
        today_button = MagicMock(triggered=[{"prop_id": "daily-report-today.n_clicks"}])
        with patch("dash.callback_context", today_button):
            self.assertEqual(namespace["follow_daily_date"](1, 1, "2026-10-05", False),
                             ("2026-10-07", True, "2026-10-07"))


class DailyReportRenderingTests(unittest.TestCase):
    def build(self, data):
        from slicing_dashboard.pages.daily_report import build_daily_report_content
        return build_daily_report_content(data, is_dark=True)

    def test_loading_zero_missing_error_and_stale_states_are_distinct(self):
        loading = payload_text(self.build(None)).lower()
        self.assertIn("loading", loading)
        zero = payload_text(self.build(report_fixture())).lower()
        self.assertIn("00:00:00", zero)
        missing = payload_text(self.build(report_fixture(available=False))).lower()
        self.assertIn("unavailable", missing)
        failed = payload_text(self.build(report_fixture(available=False, error="Capture failed"))).lower()
        self.assertTrue("could not be loaded" in failed)
        self.assertTrue("retry refresh" in failed)
        stale = payload_text(self.build(report_fixture(stale=True, error="Capture failed"))).lower()
        self.assertTrue("saved submissions" in stale)
        self.assertIn("2026-10-06", stale)

    def test_kpi_table_and_daily_bars_share_the_same_total(self):
        rows = [{"User": "Riya", "Total Tasks": 3, "Total Duration": 360,
                 "New Videos (First Time)": 60, "Same-day Rework": 120, "Old Rework": 180,
                 "Reworks": 300, "New Tasks": 1, "Same-day Rework Tasks": 1,
                 "Old Rework Tasks": 1, "Rework %": "83.3%", "RawID": "SSHD-Riya"}]
        page = self.build(report_fixture(rows=rows))
        charts = [component for component in components(page)
                  if getattr(component, "id", None) == "daily-work-chart"]
        self.assertEqual(len(charts), 1)
        bars = [trace for trace in charts[0].figure.data if trace.type == "bar"]
        self.assertAlmostEqual(sum(sum(trace.y) for trace in bars) * 3600, 180)
        tables = [component for component in components(page) if getattr(component, "data", None)
                  and component.__class__.__name__ == "DataTable"]
        table = next(table for table in tables if table.id == "daily-today-table")
        total_index = next(index for index, row in enumerate(table.data) if row.get("User") == "TOTAL")
        self.assertAlmostEqual(table.data[total_index]["Total Duration"], 0.05)
        self.assertEqual(table.tooltip_data[total_index]["Total Duration"]["value"], "00:03:00")
        duration_column = next(column for column in table.columns if column["id"] == "Total Duration")
        self.assertEqual(duration_column["type"], "numeric")
        self.assertIn("00:03:00", payload_text(page))


if __name__ == "__main__":
    unittest.main()
