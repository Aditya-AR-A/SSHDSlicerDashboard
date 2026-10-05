"""Regression tests for recovering from cached/offline dashboard data."""
from unittest.mock import MagicMock

import unittest

from slicing_dashboard.data_manager import DataManager


def manager():
    dm = DataManager.__new__(DataManager)
    dm.scraper = MagicMock()
    dm.scraper.is_authenticated = True
    dm.scraper._users = {1: {"username": "SSHD-Aditya"}}
    dm.scraper._base_url = "http://dashboard.test"
    dm._cache = {}
    dm._daily_cache = {}
    dm._snapshot_payload = {}
    dm._data = None
    dm.user_mapping = {"SSHD-Aditya": "Aditya"}
    dm.server_is_live = False
    dm.is_using_snapshot = True
    dm.last_sync_error = "Previous timeout"
    dm._save_snapshot = MagicMock()
    return dm


def assert_explicit_refresh_recovers_after_failure(efficiency):
    dm = manager()
    response = MagicMock()
    if efficiency:
        key = "eff_2_2026-09-01_2026-09-30"
        old = ({"completed_duration_seconds": 10}, [])
        fresh = ({"completed_duration_seconds": 20}, [{"username": "SSHD-Aditya"}])
        overall, page = MagicMock(), MagicMock()
        overall.json.return_value = {"summary": fresh[0]}
        page.json.return_value = {"items": fresh[1], "total": 1}
        dm.scraper._client.get.side_effect = [overall, page]
        fetch = dm.fetch_annotator_efficiency
    else:
        key = "2026-09-01_2026-09-30"
        old = {"metrics": {"duration": 10}}
        fresh = {"metrics": {"duration": 20}}
        response.json.return_value = fresh
        dm.scraper._client.get.return_value = response
        fetch = dm.fetch_dashboard_data
    dm._cache[key] = old
    assert fetch("2026-09-01", "2026-09-30") == old
    dm.scraper._client.get.assert_not_called()
    assert fetch("2026-09-01", "2026-09-30", force_refresh=True) == fresh
    assert dm._cache[key] == fresh
    assert dm.server_is_live
    assert not dm.is_using_snapshot
    assert dm.last_sync_error is None


def assert_failed_explicit_refresh_preserves_matching_cache(efficiency):
    dm = manager()
    key = "eff_2_2026-09-01_2026-09-30" if efficiency else "2026-09-01_2026-09-30"
    cached = ({"completed_duration_seconds": 10}, []) if efficiency else {"metrics": {"duration": 10}}
    dm._cache[key] = cached
    dm.scraper._client.get.side_effect = ConnectionError("Still offline")
    fetch = dm.fetch_annotator_efficiency if efficiency else dm.fetch_dashboard_data
    assert fetch("2026-09-01", "2026-09-30", force_refresh=True) == cached
    dm.scraper._client.get.assert_called_once()
    assert dm.is_using_snapshot
    assert dm.last_sync_error == "Still offline"


def assert_historical_daily_chart_bypasses_api_cache_on_refresh():
    dm = manager()
    dm.server_is_live = True
    dm._daily_cache = {"2026-09-01": {"Aditya": 10}}
    dm.fetch_annotator_efficiency = MagicMock(return_value=(
        {}, [{"username": "SSHD-Aditya", "completed_duration_seconds": 20}]
    ))
    result = dm.get_cumulative_df("2026-09-01", "2026-09-01", force_refresh=True)
    dm.fetch_annotator_efficiency.assert_called_once_with(
        start_date="2026-09-01", end_date="2026-09-01", role=2, force_refresh=True, include_summary=False
    )
    assert result.set_index("Date").loc["2026-09-01", "Aditya"] == 20


class TestRefresh(unittest.TestCase):
    def test_refresh_recovers(self):
        for efficiency in (False, True):
            with self.subTest(efficiency=efficiency):
                assert_explicit_refresh_recovers_after_failure(efficiency)

    def test_offline_fallback(self):
        for efficiency in (False, True):
            with self.subTest(efficiency=efficiency):
                assert_failed_explicit_refresh_preserves_matching_cache(efficiency)

    def test_historical_refresh(self):
        assert_historical_daily_chart_bypasses_api_cache_on_refresh()
