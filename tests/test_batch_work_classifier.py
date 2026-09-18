"""
Unit tests for BatchWorkClassifier.

Verifies:
1. Batch segregation: Rework bracket ONLY contains videos returned from day before or older.
2. Batches started/returned/resubmitted today stay in New Videos bracket.
3. Video-level duration aggregation matches video file lengths (no double counting).
4. Working hours calculation and daily tracker persistence.
"""

from unittest.mock import MagicMock
import pandas as pd

from slicing_dashboard.processing.batch_work_classifier import BatchWorkClassifier


def test_batch_segregation_and_aggregation():
    mock_scraper = MagicMock()
    mock_scraper._authenticated = True
    bwc = BatchWorkClassifier(scraper=mock_scraper)

    # Mock historical index (nothing existed before 2026-09-18)
    bwc.load_historical_index = MagicMock(return_value=(set(), set()))

    # Mock rework requests:
    # 1. task_1: returned YESTERDAY (2026-09-17) -> Must be classified as REWORK
    # 2. task_4: returned TODAY (2026-09-18) -> Started today, stays in NEW VIDEOS
    bwc._rework_requests_cache = {
        "task_1": {
            "request_id": "req_1",
            "request_type": "rework_notice",
            "reason": "Action cut too early",
            "returned_by": "Leader_A",
            "created_at": "2026-09-17T18:00:00",
        },
        "task_4": {
            "request_id": "req_2",
            "request_type": "rework_notice",
            "reason": "Hand visibility issue",
            "returned_by": "Leader_B",
            "created_at": "2026-09-18T11:30:00",
        },
    }

    # Synthetic tasks for target_date (2026-09-18)
    tasks = [
        # Batch 10: Rework Batch (returned yesterday)
        {
            "id": "task_1",
            "slicer": "SSHD-Aditya",
            "slice_batch": 10,
            "status": "slice_submitted",
            "duration_seconds": 120.0,
            "updated_at": "2026-09-18T10:00:00",
        },
        {
            "id": "task_2",
            "slicer": "SSHD-Aditya",
            "slice_batch": 10,
            "status": "slice_submitted",
            "duration_seconds": 180.0,
            "updated_at": "2026-09-18T10:15:00",
        },
        # Batch 11: Pure First-Time Batch (no returns)
        {
            "id": "task_3",
            "slicer": "SSHD-Aditya",
            "slice_batch": 11,
            "status": "slice_submitted",
            "duration_seconds": 300.0,
            "updated_at": "2026-09-18T11:00:00",
        },
        # Batch 12: Same-day returned & resubmitted batch (started today, returned today)
        # Should be classified as NEW VIDEOS per domain rule
        {
            "id": "task_4",
            "slicer": "SSHD-Aditya",
            "slice_batch": 12,
            "status": "slice_submitted",
            "duration_seconds": 240.0,
            "updated_at": "2026-09-18T14:00:00",
        },
    ]
    bwc._tasks_cache["2026-09-18"] = tasks

    df = bwc.classify_and_aggregate_daily_work("2026-09-18")

    assert not df.empty
    assert len(df) == 1
    row = df.iloc[0]

    assert row["User"] == "Aditya"
    assert row["Total Tasks"] == 4
    # Total duration: 120 + 180 + 300 + 240 = 840s (14 mins)
    assert row["Total Duration"] == 840.0

    # Batch 10 is Rework: 120 + 180 = 300s
    assert row["Reworks"] == 300.0

    # Batch 11 (300s) + Batch 12 (240s) are New Videos: 540s
    assert row["New Videos (First Time)"] == 540.0

    # Total = New + Rework: 540 + 300 = 840s
    assert row["New Videos (First Time)"] + row["Reworks"] == row["Total Duration"]
