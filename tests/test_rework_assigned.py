import unittest
from unittest.mock import MagicMock
import pandas as pd
from slicing_dashboard.data_manager import DataManager


class TestReworkAssigned(unittest.TestCase):

    def setUp(self):
        # This test exercises the overview fallback. Real startup loads an
        # unrelated persisted batch ledger before the API mocks take effect.
        self.dm = DataManager.__new__(DataManager)
        self.dm._cache = {}
        self.dm._batches_master_cache = {}
        self.dm.exempt_ids = set()
        self.dm.scraper = MagicMock()
        self.dm.scraper.is_authenticated = True
        self.dm.scraper._users = {1: {'username': 'SSHD-UserA'}, 2: {'username': 'SSHD-UserB'}}
        self.dm.scraper._get_username.side_effect = lambda uid: f"user_{uid}"
        self.dm.user_mapping = {"user_1": "UserA", "user_2": "UserB"}
        self.dm.fetch_annotator_efficiency = MagicMock(return_value=({}, []))
        self.dm.fetch_all_assigned_tasks_live = MagicMock(
            side_effect=ConnectionError("Assigned endpoint unavailable"),
        )
        self.dm._current_task_stats = MagicMock(return_value={})
        self.dm.get_assignable_pool = MagicMock(return_value={'available': True, 'duration_seconds': 0,
                                                            'task_count': 0, 'captured_at': None, 'error': None})

    def test_source_failure_never_promotes_historical_backlog_to_current_assignments(self):
        # UserA has pure backlog 1000s, but 0 rework tasks
        # UserB has pure backlog 2000s, with 500s of rework tasks
        mock_overview = {
            'breakdowns': {
                'slice_funnel': [
                    {'key': 'pending_assign', 'duration_seconds': 0, 'count': 0},
                    {'key': 'assigned', 'duration_seconds': 2500, 'count': 25},
                    {'key': 'rework', 'duration_seconds': 500, 'count': 5},
                ],
                'review_pressure': {
                    'slice_submitted_duration_seconds': 100,
                    'slice_auditor_review_duration_seconds': 200,
                    'slice_admin_review_duration_seconds': 300,
                    'slice_submitted_count': 1,
                    'slice_auditor_review_count': 2,
                    'slice_admin_review_count': 3,
                },
                'slice_user_breakdown': [
                    {
                        'user_id': 1,
                        'backlog_duration_seconds': 1000.0,
                        'backlog_count': 10,
                        'review_pending_duration_seconds': 0.0,
                        'review_pending_count': 0,
                    },
                    {
                        'user_id': 2,
                        'backlog_duration_seconds': 2000.0,
                        'backlog_count': 20,
                        'review_pending_duration_seconds': 0.0,
                        'review_pending_count': 0,
                    },
                ],
            },
            'metrics': {},
        }
        self.dm.fetch_dashboard_data = MagicMock(return_value=mock_overview)

        # Mock live rework tasks: only UserB has rework
        self.dm.get_live_rework_by_user = MagicMock(return_value={
            'UserB': {'count': 5, 'duration': 500.0}
        })

        df = self.dm.get_detailed_pending_assigned_df('2026-09-01', '2026-09-15', include_pending=False)

        # Verify UserA has NO Rework Assigned
        user_a_rework = df[(df['User'] == 'UserA') & (df['Stage'] == 'Rework Assigned')]
        self.assertTrue(user_a_rework.empty)

        assert df[df['Stage'].isin(['New Assigned', 'Rework Assigned'])].empty
        assert df.attrs['assignment_errors'] == ['New Assigned: ConnectionError']
        self.dm.scraper._client.get.assert_not_called()
        self.dm.scraper._client.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
