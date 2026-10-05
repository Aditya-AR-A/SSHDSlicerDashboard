import unittest
from collections import Counter
from unittest.mock import MagicMock

from slicing_dashboard.processing.daily_work import aggregate_daily_work
from slicing_dashboard.processing.daily_work_source import DailyWorkSource


def task(identity, submitted, batch=1, **extra):
    return {"id": identity, "slicer_id": 7, "slicer": "SSHD-A", "slice_batch": batch,
            "slice_submitted_at": submitted, "duration_seconds": 61, **extra}


class FakeSource(DailyWorkSource):
    def __init__(self, tasks, group_id=None):
        super().__init__(MagicMock(is_authenticated=True), group_id=group_id)
        self.tasks = tasks
        self.calls = []
        self.notice_pages = [{"data": [], "meta": {"total": 0}}]
        self.history = []

    def request(self, path, params=None, body=None, headers=None):
        self.calls.append((path, params, dict(body) if body else None, headers))
        if path == "/api/dashboard/batches":
            return {"scope": {"group_id": 803}, "unused_large_batch_ledger": ["unused"]}
        if path == "/api/tasks/overview":
            start = 1 if body.get("cursor") == "second" else 0
            return {"items": self.tasks[start:start + 1], "total": len(self.tasks),
                    "meta": {"has_more": start + 1 < len(self.tasks),
                             "next_cursor": "second" if start == 0 else None}}
        if path == "/api/requests":
            return self.notice_pages[params["page"] - 1]
        if path == "/api/requests/batch-return-history":
            return {"data": self.history}
        if path == "/api/review/reviewed-batches":
            return {"data": [{"reviewed_at": "2026-10-03T12:00:00"}], "meta": {"has_more": False}}
        raise AssertionError(path)

    def counts(self):
        return Counter(call[0] for call in self.calls)


class TestDailyWorkSource(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource([
            task("today", "2026-10-05T14:00:00", annotation_blob="unneeded"),
            task("yesterday", "2026-10-04T14:00:00"),
        ])

    def test_larger_cursor_pages_keep_complete_inventory_and_minimal_fields(self):
        result = self.source.fetch("2026-10-05")
        self.assertEqual([row["id"] for row in result["tasks"]], ["today", "yesterday"])
        overview = [call[2] for call in self.source.calls if call[0] == "/api/tasks/overview"]
        self.assertEqual(overview[0], {"page": 1, "page_size": 500, "group_ids": [803]})
        self.assertEqual(overview[1]["cursor"], "second")
        self.assertEqual(set(result["tasks"][0]), set(DailyWorkSource.TASK_FIELDS))
        self.assertNotIn("unused_large_batch_ledger", result["assigned_batches"])

    def test_today_and_yesterday_share_only_current_refresh_inventory(self):
        today = self.source.fetch("2026-10-05")
        yesterday = self.source.fetch("2026-10-04")
        counts = self.source.counts()
        self.assertEqual(counts["/api/tasks/overview"], 2)
        self.assertEqual(counts["/api/dashboard/batches"], 1)
        self.assertEqual(counts["/api/requests"], 1)
        self.assertEqual(counts["/api/requests/batch-return-history"], 1)
        for date, result in [("2026-10-05", today), ("2026-10-04", yesterday)]:
            rows, evidence = aggregate_daily_work(result["tasks"], result["returned_accounts"],
                                                  result["reviews"], date, lambda uid, raw: raw)
            self.assertEqual(rows.iloc[0]["Total Tasks"], 1)
            self.assertEqual(rows.iloc[0]["Total Duration"], 61)
            self.assertEqual(len(evidence), 1)
        self.source.invalidate()
        self.source.fetch("2026-10-05")
        self.assertEqual(self.source.counts()["/api/tasks/overview"], 4)
        self.assertEqual(self.source.counts()["/api/requests/batch-return-history"], 2)

    def test_seeded_live_returns_and_scope_skip_duplicate_calls(self):
        source = FakeSource([task("today", "2026-10-05T14:00:00")], group_id=803)
        source.seed_returns({7: {"data": []}, 8: {"error": "failed"}})
        result = source.fetch("2026-10-05")
        self.assertEqual(result["returned_accounts"][0]["user_id"], 7)
        self.assertEqual(source.counts()["/api/dashboard/batches"], 0)
        self.assertEqual(source.counts()["/api/requests/batch-return-history"], 0)
        source.invalidate()
        source.fetch("2026-10-05")
        self.assertEqual(source.counts()["/api/requests/batch-return-history"], 1)

    def test_prepare_leaves_histories_to_the_other_panel_and_fetch_reuses_scan(self):
        self.source.history = [{"batch_id": "returned", "legacy_batch_number": 1,
                                "returned_at": "2026-10-05T13:00:00"}]
        prepared = self.source.prepare("2026-10-05")
        self.assertEqual(len(prepared["tasks"]), 2)
        self.assertEqual(self.source.counts()["/api/tasks/overview"], 2)
        self.assertEqual(self.source.counts()["/api/requests"], 1)
        self.assertEqual(self.source.counts()["/api/requests/batch-return-history"], 0)
        self.assertEqual(self.source.counts()["/api/review/reviewed-batches"], 0)
        self.source.seed_returns({7: {"data": self.source.history}})
        result = self.source.fetch("2026-10-05")
        self.assertEqual(self.source.counts()["/api/tasks/overview"], 2)
        self.assertEqual(self.source.counts()["/api/requests"], 1)
        self.assertEqual(self.source.counts()["/api/requests/batch-return-history"], 0)
        self.assertEqual(self.source.counts()["/api/review/reviewed-batches"], 1)
        self.assertIn("returned", result["reviews"])

    def test_same_day_review_evidence_is_reused_on_repeat_view(self):
        self.source.history = [{"batch_id": "returned", "legacy_batch_number": 1,
                                "returned_at": "2026-10-05T13:00:00"}]
        result = self.source.fetch("2026-10-05")
        self.source.fetch("2026-10-05")
        self.assertEqual(self.source.counts()["/api/review/reviewed-batches"], 1)
        rows, _ = aggregate_daily_work(result["tasks"], result["returned_accounts"],
                                       result["reviews"], "2026-10-05", lambda uid, raw: raw)
        self.assertEqual(rows.iloc[0]["Old Rework"], 61)

    def test_notice_pagination_counts_unrelated_notices_before_filtering(self):
        self.source.notice_pages = [
            {"data": [{"id": "pass", "request_type": "sampling_pass_notice", "workflow_type": "slice"}],
             "meta": {"total": 2}},
            {"data": [{"id": "return", "task_id": "today", "request_type": "rework_notice",
                       "workflow_type": "slice", "created_at": "2026-10-04T12:00:00", "large_blob": "unneeded"}],
             "meta": {"total": 2}},
        ]
        result = self.source.fetch("2026-10-05")
        self.assertEqual(self.source.counts()["/api/requests"], 2)
        self.assertEqual([row["id"] for row in result["requests"]], ["return"])
        self.assertNotIn("large_blob", result["requests"][0])

    def test_missing_actual_timestamp_does_not_cache_partial_inventory(self):
        del self.source.tasks[1]["slice_submitted_at"]
        with self.assertRaisesRegex(ValueError, "actual submission timestamps"):
            self.source.fetch("2026-10-05")
        self.assertIsNone(self.source._base)
        self.source.tasks[1]["slice_submitted_at"] = "2026-10-04T14:00:00"
        self.assertEqual(len(self.source.fetch("2026-10-05")["tasks"]), 2)

    def test_empty_page_with_more_records_fails_instead_of_partial_count(self):
        self.source.tasks = []
        original = self.source.request

        def request(path, **kwargs):
            if path == "/api/tasks/overview":
                return {"items": [], "meta": {"has_more": True, "next_cursor": "next"}}
            return original(path, **kwargs)

        self.source.request = request
        with self.assertRaisesRegex(ValueError, "before all records"):
            self.source.fetch("2026-10-05")
        self.assertIsNone(self.source._base)


class OffsetSource(FakeSource):
    TASK_PAGE_SIZE = 2

    def __init__(self, fault=None, capped=False, total=None):
        super().__init__([task(str(number), "2026-10-05T14:00:00") for number in range(5)], group_id=803)
        self.fault, self.capped, self.total = fault, capped, total
        self.first_captures = 0

    def request(self, path, params=None, body=None, headers=None):
        if path != "/api/tasks/overview":
            return super().request(path, params=params, body=body, headers=headers)
        self.calls.append((path, params, dict(body), headers))
        cursor = body.get("cursor")
        number = int(cursor.split("-")[1]) if cursor else body["page"]
        if number == 1:
            self.first_captures += 1
        start = (number - 1) * self.TASK_PAGE_SIZE
        rows = self.tasks[start:start + self.TASK_PAGE_SIZE]
        total = len(self.tasks) if self.total is None else self.total
        if not cursor and number == 2 and self.first_captures == 1:
            if self.fault == "total":
                total += 1
            elif self.fault == "duplicate":
                rows = [self.tasks[0], *rows[1:]]
            elif self.fault == "short":
                rows = rows[:-1]
            elif self.fault == "unsupported":
                raise ValueError("USE_CURSOR")
        more = start + len(rows) < len(self.tasks)
        return {"items": rows, "total": total,
                "meta": {"page": number, "page_size": self.TASK_PAGE_SIZE,
                         "has_more": more, "next_cursor": f"cursor-{number + 1}" if more else None,
                         "total_capped": self.capped, "max_offset_rows": 10000}}


class TestParallelInventory(unittest.TestCase):
    def test_exact_uncapped_inventory_uses_complete_offset_pages(self):
        source = OffsetSource()
        result = source.prepare("2026-10-05")
        bodies = [call[2] for call in source.calls if call[0] == "/api/tasks/overview"]
        self.assertEqual(sorted(body["page"] for body in bodies), [1, 2, 3])
        self.assertFalse(any("cursor" in body for body in bodies))
        self.assertEqual([row["id"] for row in result["tasks"]], [str(number) for number in range(5)])

    def test_changed_total_duplicate_short_or_unsupported_offset_restarts_cursor(self):
        for fault in ("total", "duplicate", "short", "unsupported"):
            with self.subTest(fault=fault):
                source = OffsetSource(fault=fault)
                result = source.prepare("2026-10-05")
                bodies = [call[2] for call in source.calls if call[0] == "/api/tasks/overview"]
                self.assertEqual(source.first_captures, 2)
                self.assertTrue(any("cursor" in body for body in bodies))
                self.assertEqual([row["id"] for row in result["tasks"]], [str(number) for number in range(5)])

    def test_capped_or_oversized_total_stays_on_cursor_chain(self):
        for options in ({"capped": True}, {"total": 10001}):
            with self.subTest(options=options):
                source = OffsetSource(**options)
                result = source.prepare("2026-10-05")
                bodies = [call[2] for call in source.calls if call[0] == "/api/tasks/overview"]
                self.assertEqual(source.first_captures, 1)
                self.assertTrue(all("cursor" in body for body in bodies[1:]))
                self.assertEqual(len(result["tasks"]), 5)

    def test_missing_offset_support_metadata_uses_original_complete_cursor_scan(self):
        source = OffsetSource()
        original = source.request

        def request(path, **kwargs):
            result = original(path, **kwargs)
            if path == "/api/tasks/overview":
                result["meta"].pop("max_offset_rows")
            return result

        source.request = request
        result = source.prepare("2026-10-05")
        self.assertEqual(source.first_captures, 1)
        self.assertEqual(len(result["tasks"]), 5)
        self.assertTrue(any("cursor" in call[2] for call in source.calls if call[0] == "/api/tasks/overview"))


if __name__ == "__main__":
    unittest.main()
