import copy
import json
import unittest
from unittest.mock import MagicMock, patch

from dash import Dash
from plotly.utils import PlotlyJSONEncoder

from slicing_dashboard.management.periods import available_periods, validate_period_rows, today_iso
from slicing_dashboard.management.ui import register_management_callbacks, validate_mappings, assign_person, person_options, _build_mapping_table


class TestSettlementDates(unittest.TestCase):
    @patch("slicing_dashboard.management.periods.datetime")
    def test_today_uses_india_time(self, clock):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        clock.now.return_value = datetime(2026, 10, 6, 0, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(today_iso(), "2026-10-06")
        self.assertEqual(str(clock.now.call_args.args[0]), "Asia/Kolkata")

    def test_current_period_advances_without_mutating_records(self):
        records = [{"start_date": "2026-10-01", "end_date": "2026-10-02", "settled": False,
                    "label": "Old label", "value": "2026-10-01|2026-10-02"}]
        original = copy.deepcopy(records)
        first = available_periods(records, "2026-10-05")[0]
        second = available_periods(records, "2026-10-06")[0]
        self.assertEqual(first["end_date"], "2026-10-05")
        self.assertEqual(second["end_date"], "2026-10-06")
        self.assertEqual(first["value"], second["value"])
        self.assertIn("2026-10-06", second["label"])
        self.assertEqual(records, original)

    def test_closed_period_stays_fixed_and_next_period_is_current(self):
        periods = available_periods([{"start_date": "2026-09-01", "end_date": "2026-09-30", "settled": True}], "2026-10-05")
        self.assertEqual(periods[0]["end_date"], "2026-09-30")
        self.assertFalse(periods[0]["is_current"])
        self.assertEqual(periods[1]["start_date"], "2026-10-01")
        self.assertEqual(periods[1]["value"], "2026-10-01|current")

    def test_current_status_uses_today_even_with_old_end_date(self):
        periods = validate_period_rows([{"start_date": "2026-10-01", "end_date": "2026-10-02", "status": "Current"}], "2026-10-05")
        self.assertEqual(periods[0]["end_date"], "2026-10-05")

    def test_settling_period_keeps_selected_final_date(self):
        periods = validate_period_rows([{"start_date": "2026-09-01", "end_date": "2026-09-30", "status": "Settled"}], "2026-10-05")
        self.assertEqual(periods[0]["end_date"], "2026-09-30")

    def test_invalid_rows_are_reported_instead_of_dropped(self):
        for rows in (
            [{"start_date": "", "end_date": "", "status": "Unsettled"}],
            [{"start_date": "2026-10-05", "end_date": "2026-10-01", "status": "Settled"}],
            [{"start_date": "2026-10-01", "end_date": "2026-10-10", "status": "Settled"}],
            [{"start_date": "2026-09-01", "end_date": "2026-09-30", "status": "Settled"},
             {"start_date": "2026-09-30", "end_date": "2026-10-05", "status": "Current"}],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_period_rows(rows, "2026-10-05")


class TestManagementCallbacks(unittest.TestCase):
    def setUp(self):
        self.dm = MagicMock()
        self.dm.get_available_periods.return_value = available_periods([], "2026-10-05")
        self.dm.get_unassigned_users.return_value = ["SSHD-New"]
        self.dm.user_mapping_full = {"SSHD-A": {"id": "SSHD-A", "mapped_user": "Aditya", "mapping_type": "Existing", "_id": object()}}
        self.dm.exempt_ids = set()
        self.app = Dash(__name__, suppress_callback_exceptions=True)
        register_management_callbacks(self.app, self.dm)

    def callback(self, output):
        return next(v["callback"].__wrapped__ for k, v in self.app.callback_map.items() if output in k)

    @patch("slicing_dashboard.management.ui._get_admin_password", return_value="test-password")
    def test_unlock_builds_serializable_panels_and_hides_password_card(self, _):
        for kind in ("mapping", "settlement"):
            panel, error, style = self.callback(f"{kind}-content.children")(1, 0, "test-password")
            encoded = json.dumps(panel, cls=PlotlyJSONEncoder)
            self.assertIn(f"{kind}-table", encoded)
            self.assertNotIn('"_id"', encoded)
            self.assertEqual(style, {"display": "none"})
            self.assertEqual(error, "")

    @patch("slicing_dashboard.management.ui._get_admin_password", return_value="test-password")
    def test_wrong_password_never_writes(self, _):
        for kind in ("mapping", "settlement"):
            result = self.callback(f"{kind}-save-output.children")(1, 0, "incorrect", [])
            self.assertIn("Incorrect", result.children)
        self.dm.db.save_settlement_periods.assert_not_called()
        self.dm.db.save_user_mappings.assert_not_called()

    def test_search_escapes_text_and_combines_status(self):
        query = self.callback("mapping-table.filter_query")('SSHD-"New', "New")
        self.assertIn('\\"New', query)
        self.assertIn('{mapping_type} = "New"', query)

    @patch("slicing_dashboard.management.ui.ctx")
    @patch("slicing_dashboard.management.ui.today_iso", return_value="2026-10-06")
    def test_midnight_updates_current_row_only(self, _, context):
        context.triggered_id = "settlement-date-interval"
        rows = [{"start_date": "2026-10-01", "end_date": "2026-10-05", "status": "Current"},
                {"start_date": "2026-09-01", "end_date": "2026-09-30", "status": "Settled"}]
        updated = self.callback("settlement-table.data")(0, 1, rows)
        self.assertEqual(updated[0]["end_date"], "2026-10-06")
        self.assertEqual(updated[1]["end_date"], "2026-09-30")
        self.assertEqual(rows[0]["end_date"], "2026-10-05")


class TestMappingValidation(unittest.TestCase):
    def test_existing_person_can_be_selected_for_unmapped_account(self):
        rows = [{"id": "SSHD-A", "mapped_user": "Aditya", "mapping_type": "Existing"},
                {"id": "SSHD-New", "mapped_user": "Unassigned", "mapping_type": "New"}]
        updated, name = assign_person(rows, "SSHD-New", "Aditya", "Ignored new name")
        self.assertEqual(updated[1]["mapped_user"], "Aditya")
        self.assertEqual(updated[1]["mapping_type"], "Existing")
        self.assertEqual(rows[1]["mapping_type"], "New")
        self.assertEqual(_build_mapping_table(rows).columns[1]["presentation"], "dropdown")

    def test_new_person_becomes_a_reusable_option(self):
        rows = [{"id": "SSHD-New", "mapped_user": "Unassigned", "mapping_type": "New"}]
        updated, name = assign_person(rows, "SSHD-New", "__new__", "  Sheetal  ")
        self.assertEqual(name, "Sheetal")
        self.assertEqual(person_options(updated), [{"label": "Sheetal", "value": "Sheetal"}])
        self.assertEqual(validate_mappings(updated)[0]["mapping_type"], "Existing")

    def test_assignment_rejects_missing_account_or_person(self):
        rows = [{"id": "SSHD-New", "mapped_user": "Unassigned", "mapping_type": "New"}]
        for account, person, new in ((None, "Aditya", None), ("SSHD-New", None, None), ("SSHD-New", "__new__", " "), ("missing", "Aditya", None)):
            with self.subTest(account=account, person=person), self.assertRaises(ValueError):
                assign_person(rows, account, person, new)

    def test_existing_name_is_reused_when_case_differs(self):
        rows = [{"id": "SSHD-A", "mapped_user": "Aditya", "mapping_type": "Existing"}]
        updated, name = assign_person(rows, "SSHD-A", "__new__", "aditya")
        self.assertEqual(name, "Aditya")

    def test_duplicate_accounts_are_case_insensitive(self):
        with self.assertRaisesRegex(ValueError, "more than once"):
            validate_mappings([{"id": "SSHD-A", "mapped_user": "Aditya", "mapping_type": "Existing"},
                               {"id": "sshd-a", "mapped_user": "Aditya", "mapping_type": "Existing"}])

    def test_unassigned_accounts_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "person"):
            validate_mappings([{"id": "SSHD-A", "mapped_user": "", "mapping_type": "Existing"}])
        rows = validate_mappings([{"id": "SSHD-A", "mapped_user": "", "mapping_type": "New"}])
        self.assertEqual(rows[0]["mapped_user"], "Unassigned")
