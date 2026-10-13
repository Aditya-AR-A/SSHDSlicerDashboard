import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd

from slicing_dashboard.processing.daily_work import aggregate_daily_work, daily_submissions, format_video_seconds
from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart
from slicing_dashboard.data_manager import DataManager


def task(identity, user="SSHD-A", batch=1, submitted="2026-10-05T14:00:00", duration=60, **extra):
    return dict(id=identity, slicer=user, slicer_id=1, slice_batch=batch,
                slice_submitted_at=submitted, duration_seconds=duration, **extra)


class TestDailyWork(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = patch('slicing_dashboard.config.DATA_DIR', Path(temporary.name))
        directory.start()
        self.addCleanup(directory.stop)
        # Failure/fallback fixtures exercise yesterday's allowed inventory read.
        # Keep that boundary fixed as the real India calendar advances.
        clock = patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06')
        clock.start()
        self.addCleanup(clock.stop)

    def test_daily_format_retains_seconds(self):
        self.assertEqual(format_video_seconds(103341), '28:42:21')

    def aggregate(self, tasks, returns=None, reviews=None):
        return aggregate_daily_work(tasks, returns or [], reviews or {}, "2026-10-05", lambda uid, raw: raw)

    def test_review_update_is_not_today_submission(self):
        rows, _ = self.aggregate([task("old", submitted="2026-10-04T15:00:00", updated_at="2026-10-05T14:00:00", status="slice_completed")])
        self.assertTrue(rows.empty)

    def test_pending_rework_without_submission_is_excluded(self):
        rows, _ = self.aggregate([task("pending", submitted=None, status="slice_rework")])
        self.assertTrue(rows.empty)

    def test_india_midnight_boundary(self):
        selected = daily_submissions([task("prior", submitted="2026-10-05T02:29:59"),
                                      task("today", submitted="2026-10-05T02:30:00"),
                                      task("late", submitted="2026-10-06T02:29:59"),
                                      task("tomorrow", submitted="2026-10-06T02:30:00")], "2026-10-05")
        self.assertEqual([t["id"] for t in selected], ["today", "late"])

    def test_batch_returns_and_reviews_separate_three_buckets(self):
        tasks = [task("fresh", batch=1, duration=30), task("same", batch=2, duration=60), task("old", batch=3, duration=90)]
        returns = [{"username": "SSHD-A", "data": [
            {"batch_id": "same", "legacy_batch_number": 2, "returned_at": "2026-10-05T13:00:00"},
            {"batch_id": "old", "legacy_batch_number": 3, "returned_at": "2026-10-05T13:00:00"}]}]
        reviews = {"old": {"data": [{"reviewed_at": "2026-09-25T15:00:00+08:00"}]}}
        result, evidence = self.aggregate(tasks, returns, reviews)
        row = result.iloc[0]
        self.assertEqual(row["Total Tasks"], 3)
        self.assertEqual(row["Total Duration"], 90)
        self.assertEqual(row["New Videos (First Time)"], 30)
        self.assertEqual(row["Same-day Rework"], 60)
        self.assertEqual(row["Old Rework"], 90)
        self.assertEqual(len(evidence), 3)

    def test_return_after_submission_does_not_count_as_rework_done(self):
        returns = [{"username": "SSHD-A", "data": [{"batch_id": "batch", "legacy_batch_number": 1, "returned_at": "2026-10-05T16:00:00"}]}]
        result, _ = self.aggregate([task("fresh")], returns)
        self.assertEqual(result.iloc[0]["Reworks"], 0)

    def test_individual_rework_notice_is_included_but_pass_notice_is_not(self):
        requests = [{"id": "return", "request_type": "rework_notice", "workflow_type": "slice", "task_id": "old", "created_at": "2026-10-04T12:00:00"},
                    {"id": "pass", "request_type": "sampling_pass_notice", "workflow_type": "slice", "task_id": "fresh", "created_at": "2026-10-04T12:00:00"}]
        rows, evidence = aggregate_daily_work([task("old"), task("fresh")], [], {}, "2026-10-05", lambda uid, raw: raw, requests=requests)
        self.assertEqual(rows.iloc[0]["Old Rework"], 60)
        self.assertEqual(rows.iloc[0]["New Videos (First Time)"], 60)
        self.assertEqual(evidence[0]["individual_return_request_ids"], ["return"])

    def test_same_batch_number_on_other_account_does_not_mix(self):
        returns = [{"username": "SSHD-B", "data": [{"batch_id": "batch", "legacy_batch_number": 1, "returned_at": "2026-10-04T12:00:00"}]}]
        result, _ = self.aggregate([task("fresh")], returns)
        self.assertEqual(result.iloc[0]["Reworks"], 0)

    def test_duplicate_task_is_counted_once(self):
        result, _ = self.aggregate([task("same"), task("same")])
        self.assertEqual(result.iloc[0]["Total Tasks"], 1)
        self.assertEqual(result.iloc[0]["Total Duration"], 60)

    def test_chart_stacked_work_excludes_separate_old_rework_marker(self):
        rows = pd.DataFrame([{"User": "Aditya", "New Work Duration": 30, "Same-day Rework Duration": 60,
                              "Old Rework Duration": 90, "Total Work Duration": 180}])
        figure = build_error_rework_chart(rows, "Today", True)
        self.assertEqual([t.name for t in figure.data], ["Fresh Work", "Same-day Rework", "Old Rework (excluded)"])
        self.assertAlmostEqual(sum(t.y[0] for t in figure.data if t.type == 'bar') * 3600, 90)
        self.assertEqual(figure.data[2].type, 'scatter')
        self.assertEqual(figure.data[2].customdata[0][1], '00:01:30')

    @patch("slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch", side_effect=ConnectionError("Offline"))
    def test_failure_never_uses_another_dates_results(self, _):
        dm = DataManager.__new__(DataManager)
        dm.scraper = MagicMock()
        dm._cache = {"verified_daily_work_2026-10-04": {"rows": [{"User": "Aditya", "Total Duration": 100}]}}
        result = dm.get_todays_work_df("2026-10-05", force_refresh=True)
        self.assertTrue(result.empty)
        self.assertEqual(result.attrs["error"], "Offline")

    @patch("slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch", side_effect=ConnectionError("Offline"))
    def test_matching_snapshot_is_marked_stale(self, _):
        dm = DataManager.__new__(DataManager)
        dm.scraper = MagicMock()
        dm._cache = {"verified_daily_work_2026-10-05": {"rows": [{"User": "Aditya", "Total Duration": 100}],
                                                         "metadata": {"captured_at": "2026-10-05T10:00:00+05:30"}}}
        result = dm.get_todays_work_df("2026-10-05", force_refresh=True)
        self.assertEqual(result.iloc[0]["Total Duration"], 100)
        self.assertTrue(result.attrs["is_snapshot"])
