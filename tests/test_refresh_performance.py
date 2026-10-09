"""Call-budget regressions for dashboard refreshes; no external services required."""
import tempfile
import json
import unittest
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from scripts.migrate_compressed_snapshot_key import migrate_snapshot_key


def manager(directory):
    dm = DataManager.__new__(DataManager)
    dm.scraper = MagicMock()
    dm.scraper.is_authenticated = True
    dm.scraper._base_url = 'http://dashboard.test'
    dm.scraper._users = {1: {'username': 'SSHD-Aditya'}}
    dm.db = MagicMock()
    dm._cache = {}
    dm._daily_cache = {}
    dm._snapshot_payload = {}
    dm._batch_returns_cache = []
    dm._batches_master_cache = {}
    dm._batches_master_synced_dates = set()
    dm._last_batch_returns_sync = None
    dm._last_batches_sync = None
    dm._batch_returns_path = Path(directory) / 'returns.json'
    dm._batches_master_path = Path(directory) / 'batches.json'
    dm._snapshot_path = Path(directory) / 'snapshot.json'
    dm._data = None
    dm.user_mapping = {'SSHD-Aditya': 'Aditya'}
    dm.server_is_live = True
    dm.is_using_snapshot = False
    dm.last_sync_error = None
    return dm


def response(data):
    result = MagicMock(status_code=200)
    result.json.return_value = data
    return result


class TestRefreshPerformance(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        data_path = patch('slicing_dashboard.config.DATA_DIR', Path(self.directory.name))
        data_path.start()
        self.addCleanup(data_path.stop)
        self.dm = manager(self.directory.name)

    def test_repeated_forced_reads_fetch_once_and_save_once(self):
        dm = self.dm
        dm.scraper._client.get.return_value = response({'metrics': {'completed': 5}})
        with dm.refresh_scope():
            first = dm.fetch_dashboard_data('2026-10-01', '2026-10-05', True)
            with dm.refresh_scope():
                second = dm.fetch_dashboard_data('2026-10-01', '2026-10-05', force_refresh=True)
            self.assertEqual(first, second)
            dm.db.save_dashboard_snapshot.assert_not_called()
        self.assertEqual(dm.scraper._client.get.call_count, 1)
        self.assertEqual(dm.db.save_dashboard_snapshot.call_count, 1)
        self.assertEqual(dm.last_refresh_profile['memoized_hits'], 1)
        self.assertEqual([read['method'] for read in dm.last_refresh_profile['reads']], ['fetch_dashboard_data'])
        self.assertTrue((Path(self.directory.name) / 'reports' / 'performance-audit' / 'last-refresh-profile.json').exists())
        with dm.refresh_scope():
            dm.fetch_dashboard_data('2026-10-01', '2026-10-05', True)
        self.assertEqual(dm.scraper._client.get.call_count, 2)
        self.assertEqual(dm.db.save_dashboard_snapshot.call_count, 2)

    def test_full_and_items_only_metrics_share_fresh_rows(self):
        dm = self.dm
        summary = {'completed_count': 1}
        items = [{'username': 'SSHD-Aditya', 'completed_count': 1}]
        dm.scraper._client.get.side_effect = [response({'summary': summary}), response({'items': items, 'total': 1})]
        with dm.refresh_scope():
            self.assertEqual(dm.fetch_annotator_efficiency('2026-10-05', '2026-10-05', force_refresh=True), (summary, items))
            self.assertEqual(dm.fetch_annotator_efficiency('2026-10-05', '2026-10-05', force_refresh=True, include_summary=False), (summary, items))
            dm.get_user_breakdown_df('2026-10-05', '2026-10-05', force_refresh=True)
        self.assertEqual(dm.scraper._client.get.call_count, 2)
        self.assertEqual(dm.db.save_dashboard_snapshot.call_count, 1)
        # A subsequent display interaction uses the valid summary cache.
        self.assertEqual(dm.fetch_annotator_efficiency('2026-10-05', '2026-10-05'), (summary, items))
        self.assertEqual(dm.scraper._client.get.call_count, 2)

    def test_items_first_can_later_fetch_summary_without_repeating_items(self):
        dm = self.dm
        dm.scraper._client.get.side_effect = [response({'items': [], 'total': 0}), response({'summary': {'total_count': 10}})]
        with dm.refresh_scope():
            dm.fetch_annotator_efficiency('2026-10-05', '2026-10-05', force_refresh=True, include_summary=False)
            result = dm.fetch_annotator_efficiency('2026-10-05', '2026-10-05', force_refresh=True)
        self.assertEqual(result[0], {'total_count': 10})
        self.assertEqual(dm.scraper._client.get.call_count, 2)

    def test_daily_chart_workers_skip_summary_and_flush_once(self):
        dm = self.dm
        dm.scraper._client.get.return_value = response({'items': [{'username': 'SSHD-Aditya', 'completed_duration_seconds': 10}], 'total': 1})
        with dm.refresh_scope():
            result = dm.get_cumulative_df('2026-09-01', '2026-09-03', force_refresh=True)
        self.assertEqual(result['Aditya'].tolist(), [10, 20, 30])
        self.assertEqual(dm.scraper._client.get.call_count, 3)
        self.assertTrue(all(call.args[0].endswith('/annotator-efficiency') for call in dm.scraper._client.get.call_args_list))
        self.assertEqual(dm.db.save_dashboard_snapshot.call_count, 1)

    def test_concurrent_identical_reads_share_one_fetch(self):
        dm = self.dm
        dm.scraper._client.get.return_value = response({'metrics': {'completed': 5}})
        with dm.refresh_scope():
            worker = dm._refresh_worker(lambda _: dm.fetch_dashboard_data('2026-10-05', '2026-10-05', True))
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(worker, range(10)))
        self.assertEqual(len(results), 10)
        self.assertEqual(dm.scraper._client.get.call_count, 1)

    def test_assigned_inventory_is_cached_for_display_interactions(self):
        dm = self.dm
        dm.scraper._client.get.return_value = response({'data': [{'id': 'task-1', 'status': 'slice_assigned', 'slicer': 'SSHD-Aditya', 'duration_seconds': 60}], 'meta': {'has_more': False}})
        first = dm.fetch_all_assigned_tasks_live(force_refresh=True)
        second = dm.fetch_all_assigned_tasks_live()
        self.assertEqual(first, second)
        self.assertEqual(second['Aditya']['raw_ids'], {'SSHD-Aditya'})
        dm.scraper._client.get.assert_called_once()

    @patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-05')
    def test_today_and_yesterday_share_source_only_within_refresh(self, _):
        dm = self.dm
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource') as source_class, patch('slicing_dashboard.config.DATA_DIR', Path(self.directory.name)):
            source = source_class.return_value
            source.fetch.side_effect = lambda day: {'tasks': [], 'returned_accounts': [], 'reviews': {}, 'requests': [], 'captured_at': '2026-10-05T16:00:00+05:30'}
            with dm.refresh_scope():
                dm.get_todays_work_df('2026-10-05', force_refresh=True)
                dm.get_todays_work_df('2026-10-04', force_refresh=True)
            self.assertEqual(source_class.call_count, 1)
            self.assertEqual(source.fetch.call_count, 2)
            source.invalidate.assert_called_once()
            with dm.refresh_scope():
                dm.get_todays_work_df('2026-10-05', force_refresh=True)
                yesterday = dm.get_todays_work_df('2026-10-04', force_refresh=True)
            self.assertEqual(source_class.call_count, 2)
            self.assertEqual(source.fetch.call_count, 4)
            self.assertTrue(yesterday.attrs['closed'])

    def test_recent_ledger_and_empty_returns_do_not_refresh_on_display_events(self):
        dm = self.dm
        dm._last_batch_returns_sync = datetime.now()
        dm._last_batches_sync = datetime.now()
        dm._batches_master_synced_dates = {'2026-09-01', '2026-09-02'}
        self.assertEqual(dm.sync_batch_returns(), [])
        self.assertEqual(dm.sync_batches_master('2026-09-01', '2026-09-02'), {})
        dm.scraper._client.get.assert_not_called()

    def test_successful_empty_day_is_covered_but_failed_day_is_not(self):
        dm = self.dm
        dm._last_batch_returns_sync = datetime.now()
        success = response({'breakdowns': {'batches': []}})
        failure = MagicMock(status_code=503)
        dm.scraper._client.get.side_effect = [success, failure]
        with dm.refresh_scope():
            dm.sync_batches_master('2026-09-01', '2026-09-02')
        self.assertEqual(dm._batches_master_synced_dates, {'2026-09-01'})
        self.assertEqual(dm.scraper._client.get.call_count, 2)
        dm.scraper._client.get.side_effect = [success]
        dm.sync_batches_master('2026-09-01', '2026-09-01')
        self.assertEqual(dm.scraper._client.get.call_count, 2)
        self.assertIsNone(dm._batch_source_error)

    def test_batch_mapping_is_resolved_again_without_api_reads(self):
        dm = self.dm
        ledger = {'username': 'SSHD-Aditya', 'canonical_user': 'Old name', 'assignee_id': 1}
        self.assertEqual(dm._batch_canonical_name(ledger), 'Aditya')
        dm.user_mapping['SSHD-Aditya'] = 'New name'
        self.assertEqual(dm._batch_canonical_name(ledger), 'New name')
        dm.scraper._client.get.assert_not_called()

    def test_pending_view_uses_pool_inventory_instead_of_overview(self):
        dm = self.dm
        dm.fetch_annotator_efficiency = MagicMock(return_value=({}, []))
        dm.get_live_rework_by_user = MagicMock(return_value={})
        dm.fetch_all_assigned_tasks_live = MagicMock(return_value={})
        dm._current_task_stats = MagicMock(return_value={})
        dm.fetch_dashboard_data = MagicMock()
        dm.get_assignable_pool = MagicMock(return_value={'available': True, 'duration_seconds': 0,
                                                       'task_count': 0, 'captured_at': None, 'error': None})
        raw = {'metrics': {'overview_slice_assignable_remaining_duration_seconds': 999},
               'breakdowns': {'slice_funnel': [{'key': 'pending_assign', 'duration_seconds': 99, 'count': 2}]}}
        result = dm.get_detailed_pending_assigned_df('2020-01-01', '2026-10-05', overview_data=raw)
        pool = result[result['User'] == 'Assignable Pool'].iloc[0]
        self.assertEqual(pool['Duration'], 0)
        self.assertEqual(pool['Count'], 0)
        dm.fetch_dashboard_data.assert_not_called()


class TestSnapshotWriteBudget(unittest.TestCase):
    def test_granular_ledgers_are_bulk_written_only_when_changed(self):
        db = DatabaseManager.__new__(DatabaseManager)
        db._connected = True
        db.engine = None
        db.mongo_db = {name: MagicMock() for name in ('dashboard_snapshot', 'batches_master', 'batch_returns')}
        batches = [{'batch_id': f'batch-{index}', 'task_count': 52} for index in range(1100)]
        returns = [{'event_id': 'return-1', 'batch_id': 'batch-1'}, {'event_id': 'return-2', 'batch_id': 'batch-1'}]
        snapshot = {'batches_master_records': batches, 'batch_returns_records': returns}
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        self.assertEqual(db.mongo_db['batches_master'].bulk_write.call_count, 3)
        self.assertEqual(db.mongo_db['batch_returns'].bulk_write.call_count, 1)
        operations = db.mongo_db['batch_returns'].bulk_write.call_args.args[0]
        self.assertEqual({operation._doc['_id'] for operation in operations}, {'return-1', 'return-2'})
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        self.assertEqual(db.mongo_db['batches_master'].bulk_write.call_count, 3)
        self.assertEqual(db.mongo_db['batch_returns'].bulk_write.call_count, 1)
        batches[0]['task_count'] = 53
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        self.assertEqual(db.mongo_db['batches_master'].bulk_write.call_count, 4)
        self.assertEqual(len(db.mongo_db['batches_master'].bulk_write.call_args.args[0]), 1)
        db.mongo_db['batches_master'].replace_one.assert_not_called()
        db.mongo_db['batch_returns'].replace_one.assert_not_called()

    def test_snapshot_compression_roundtrip_and_legacy_load(self):
        db = DatabaseManager.__new__(DatabaseManager)
        db._connected = True
        db.engine = None
        db.mongo_db = {name: MagicMock() for name in ('dashboard_snapshot', 'batches_master', 'batch_returns')}
        snapshot = {'cache': {'range': {'names': ['Aditya', 'रिय़ा'], 'duration': 60.25, 'missing': None, 'active': True}},
                    'batches_master_records': [], 'batch_returns_records': []}
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        envelope = db.mongo_db['dashboard_snapshot'].replace_one.call_args.args[1]
        self.assertEqual(envelope['_id'], 'latest-compressed-v1')
        self.assertEqual(db.mongo_db['dashboard_snapshot'].replace_one.call_args.args[0], {'_id': 'latest-compressed-v1'})
        self.assertEqual(envelope['snapshot_encoding'], 'zlib-base64-v1')
        self.assertNotIn('cache', envelope)
        db.mongo_db['dashboard_snapshot'].find_one.return_value = envelope
        self.assertEqual(db.load_dashboard_snapshot(), snapshot)
        db.mongo_db['dashboard_snapshot'].find_one.assert_called_with({'_id': 'latest-compressed-v1'})
        db.mongo_db['dashboard_snapshot'].find_one.side_effect = [None, dict(snapshot, _id='latest')]
        self.assertEqual(db.load_dashboard_snapshot(), snapshot)
        db.mongo_db['dashboard_snapshot'].find_one.assert_called_with({'_id': 'latest'})

    def test_migration_restores_legacy_with_compare_and_swap_guard(self):
        db = DatabaseManager.__new__(DatabaseManager)
        db._connected = True
        db.engine = None
        db.mongo_db = {name: MagicMock() for name in ('dashboard_snapshot', 'batches_master', 'batch_returns')}
        snapshot = {'cache': {'completed_seconds': 60}}
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        envelope = dict(db.mongo_db['dashboard_snapshot'].replace_one.call_args.args[1], _id='latest')
        collection = db.mongo_db['dashboard_snapshot']
        collection.find_one.return_value = envelope
        collection.update_one.return_value.upserted_id = 'latest-compressed-v1'
        collection.replace_one.return_value.matched_count = 1
        outcome = migrate_snapshot_key(db)
        self.assertTrue(outcome['legacy_restored'])
        self.assertEqual(collection.update_one.call_args.args[0], {'_id': 'latest-compressed-v1'})
        self.assertEqual(collection.update_one.call_args.args[1]['$setOnInsert']['_id'], 'latest-compressed-v1')
        compare, legacy = collection.replace_one.call_args.args
        self.assertEqual(compare, {'_id': 'latest', 'snapshot_encoding': 'zlib-base64-v1', 'updated_at': envelope['updated_at']})
        self.assertEqual(legacy['cache'], snapshot['cache'])
        self.assertNotIn('snapshot_payload', legacy)
        self.assertFalse(collection.replace_one.call_args.kwargs['upsert'])
        # A concurrent writer wins; repair never creates/replaces unmatched latest.
        collection.replace_one.return_value.matched_count = 0
        self.assertFalse(migrate_snapshot_key(db)['legacy_restored'])

    def test_bookkeeping_timestamp_does_not_rewrite_unchanged_ledger(self):
        db = DatabaseManager.__new__(DatabaseManager)
        db._connected = True
        db.engine = None
        db.mongo_db = {name: MagicMock() for name in ('dashboard_snapshot', 'batches_master', 'batch_returns')}
        batch = {'batch_id': 'batch-1', 'status': 'pending_review', 'task_count': 52, 'last_updated': '2026-10-05T16:00:00'}
        snapshot = {'batches_master_records': [batch], 'batch_returns_records': []}
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        batch['last_updated'] = '2026-10-05T17:00:00'
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        self.assertEqual(db.mongo_db['batches_master'].bulk_write.call_count, 1)
        batch['status'] = 'completed'
        self.assertTrue(db.save_dashboard_snapshot(snapshot))
        self.assertEqual(db.mongo_db['batches_master'].bulk_write.call_count, 2)


if __name__ == '__main__':
    unittest.main()
