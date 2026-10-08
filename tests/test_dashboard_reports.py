"""Daily report semantics and durable observations without service connections."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pymongo.errors import DuplicateKeyError

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.reporting.dashboard_reports import (
    aggregate_observations, date_range, day_for_ui, day_over_day, merge_daily_reports,
    prepare_daily_table, prepare_daily_work_chart_rows, prepare_team_composition_rows,
    prepare_user_comparison_rows, report_users, summarize_daily, unavailable_day,
)


def evidence(identity='a', day='2026-10-06', bucket='Fresh', seconds=60, hour='12', user='A', raw='SSHD-A'):
    return {'task_id': identity, 'username': raw, 'user': user, 'submitted_at': f'{day}T{hour}:00:00+05:30',
            'duration_seconds': seconds, 'bucket': bucket, 'slice_batch': 1}


def capture(tasks, day='2026-10-06', observed_on=None, hour='14'):
    return {'date': day, 'tasks': tasks, 'metadata': {'target_date': day,
            'captured_at': f'{observed_on or day}T{hour}:00:00+05:30',
            'observed_on': observed_on or day, 'available': True}}


def task(identity='a', submitted='2026-10-06T15:00:00+08:00', raw='SSHD-A', seconds=60):
    return {'id': identity, 'slicer': raw, 'slicer_id': 1, 'slice_batch': 1,
            'slice_submitted_at': submitted, 'duration_seconds': seconds}


def source(tasks, day='2026-10-06'):
    return {'tasks': tasks, 'returned_accounts': [], 'reviews': {}, 'requests': [],
            'captured_at': f'{day}T16:00:00+05:30'}


class TestDailyReportHelpers(unittest.TestCase):
    def test_calendar_range_is_exactly_thirty_days_across_months(self):
        days = date_range('2026-10-06')
        self.assertEqual(len(days), 30)
        self.assertEqual((days[0], days[-1]), ('2026-09-07', '2026-10-06'))

    def test_category_arithmetic_kpis_table_and_chart_use_same_seconds(self):
        record = merge_daily_reports(None, capture([
            evidence('new', seconds=30), evidence('same', bucket='Same-day Rework', seconds=60),
            evidence('old', bucket='Old Rework', seconds=90),
        ]))
        day = day_for_ui(record, record['date'])
        summary = summarize_daily(day)
        self.assertEqual(summary['total_seconds'], 90)
        self.assertEqual(summary['new_seconds'] + summary['same_day_rework_seconds'], 90)
        self.assertEqual(summary['old_rework_seconds'], 90)
        self.assertEqual(summary['active_users'], 1)
        self.assertEqual(summary['rework_percentage'], 60 / 90 * 100)
        self.assertEqual(prepare_daily_table(day)[-1]['Total Duration'], '00:01:30')
        self.assertEqual(prepare_daily_table(day, numeric_durations=True)[-1]['Total Duration'], 90 / 3600)
        chart = prepare_daily_work_chart_rows(day)
        self.assertEqual(chart.iloc[0]['Total Work Duration'], 90)
        self.assertEqual(prepare_team_composition_rows([day])[0]['total_seconds'], 90)

    def test_unavailable_empty_and_zero_work_user_remain_distinct(self):
        unknown = unavailable_day('2026-10-05', 'Offline')
        zero = day_for_ui(merge_daily_reports(None, capture([])), '2026-10-06')
        self.assertFalse(day_for_ui(unknown, unknown['date'])['metadata']['available'])
        self.assertIsNone(summarize_daily(unknown)['total_seconds'])
        self.assertEqual(summarize_daily(zero)['total_seconds'], 0)
        prepared = prepare_user_comparison_rows([unknown, zero], ['A', 'B'])
        self.assertEqual([row['total_seconds'] for row in prepared], [None, None, 0, 0])
        self.assertIsNone(day_over_day(zero, zero)['percentage'])
        self.assertIsNone(day_over_day(zero, unknown)['difference_seconds'])

    def test_roster_includes_zero_work_people_and_named_unmapped_accounts(self):
        names = report_users({'SSHD-A': 'A', 'SSHD-A2': 'A', 'SSHD-B': 'B', 'test': 'Test',
                              'unmapped': 'Unassigned', 'exempt': 'Person excluded'},
                             {'available_users': ['Exempt', 'TOTAL', 'A']},
                             [{'rows': [{'User': 'Named unmapped account'}]}],
                             [{'id': 'exempt', 'mapping_type': 'Exempt'}])
        self.assertEqual(names, ['A', 'B', 'Named unmapped account'])

    def test_current_mappings_regroup_preserved_aliases_and_exclude_accounts(self):
        tasks = [evidence('a'), evidence('b', raw='SSHD-A2'), evidence('c', raw='test')]
        mapping = {'SSHD-A': 'A', 'SSHD-A2': 'A', 'test': 'Exempt'}
        rows = aggregate_observations(tasks, lambda uid, raw: mapping[raw])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Total Tasks'], 2)
        self.assertEqual(rows[0]['RawID'], 'SSHD-A,SSHD-A2')

    def test_malformed_duration_or_date_is_not_a_valid_zero_capture(self):
        for tasks in ([evidence(seconds=-1)], [evidence(seconds=float('inf'))],
                      [evidence(day='2026-10-05')]):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                merge_daily_reports(None, capture(tasks))


class TestIncrementalEvidence(unittest.TestCase):
    def test_repeat_observation_unions_disappeared_tasks_without_duplicate_hours(self):
        first = merge_daily_reports(None, capture([evidence('a'), evidence('b')]))
        updated = merge_daily_reports(first, capture([evidence('b'), evidence('c')], hour='15'))
        repeated = merge_daily_reports(updated, capture([evidence('b'), evidence('c')], hour='15'))
        self.assertEqual(updated['rows'][0]['Total Tasks'], 3)
        self.assertEqual(updated['rows'][0]['Total Duration'], 180)
        self.assertEqual(updated, repeated)

    def test_latest_same_day_submission_replaces_category_and_identity_once(self):
        first = merge_daily_reports(None, capture([evidence()], hour='13'))
        changed = merge_daily_reports(first, capture([
            evidence(bucket='Same-day Rework', seconds=90, hour='15', raw='SSHD-A2')], hour='16'))
        self.assertEqual(changed['rows'][0]['Total Tasks'], 1)
        self.assertEqual(changed['rows'][0]['New Videos (First Time)'], 0)
        self.assertEqual(changed['rows'][0]['Same-day Rework'], 90)
        older = merge_daily_reports(changed, capture([evidence()], hour='13'))
        self.assertEqual(older['rows'], changed['rows'])

    def test_equal_submission_timestamp_uses_latest_classification_observation(self):
        first = merge_daily_reports(None, capture([evidence()], hour='13'))
        changed = merge_daily_reports(first, capture([evidence(bucket='Old Rework')], hour='15'))
        delayed = merge_daily_reports(changed, capture([evidence()], hour='14'))
        self.assertEqual(delayed['rows'][0]['Old Rework'], 60)

    def test_later_inventory_never_overwrites_previous_date(self):
        closed = merge_daily_reports(None, capture([evidence(day='2026-10-05')], day='2026-10-05'))
        later = capture([], day='2026-10-05', observed_on='2026-10-06')
        self.assertEqual(merge_daily_reports(closed, later), closed)
        next_day = merge_daily_reports(None, capture([evidence('a', bucket='Old Rework')]))
        self.assertEqual(closed['rows'][0]['Total Duration'], 60)
        self.assertEqual(next_day['rows'][0]['Total Duration'], 0)
        self.assertEqual(closed['rows'][0]['New Videos (First Time)'], 60)

    def test_only_a_dated_next_day_reconciliation_can_update_closed_evidence(self):
        closed = merge_daily_reports(None, capture([evidence('early', day='2026-10-05')], day='2026-10-05'))
        incoming = capture([evidence('late', day='2026-10-05', seconds=120)],
                           day='2026-10-05', observed_on='2026-10-06')
        incoming['metadata'].update(capture_kind='day_end_reconciliation', target_date='2026-10-05')
        reconciled = merge_daily_reports(closed, incoming)
        self.assertEqual(reconciled['rows'][0]['Total Duration'], 180)
        self.assertTrue(reconciled['metadata']['closed'])
        self.assertEqual(merge_daily_reports(reconciled, incoming)['rows'], reconciled['rows'])
        incoming['metadata']['observed_on'] = '2026-10-07'
        self.assertEqual(merge_daily_reports(closed, incoming), closed)


class TestManagerDailyReports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for replacement in (patch('slicing_dashboard.config.DATA_DIR', self.directory),
                            patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06')):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.dm = self.manager()

    def manager(self):
        manager = DataManager.__new__(DataManager)
        manager.scraper = MagicMock()
        manager.db = MagicMock()
        manager.db.load_daily_work_reports.return_value = []
        manager.db.save_daily_work_report.return_value = False
        manager._cache = {}
        manager.user_mapping = {'SSHD-A': 'A', 'SSHD-B': 'B'}
        manager.user_mapping_full = {}
        manager._snapshot_payload = {}
        return manager

    def test_only_today_yesterday_capture_and_earlier_days_are_unavailable(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
                   return_value=source([task()])) as fetch:
            payload = self.dm.get_daily_report_data(force_refresh=True)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ['2026-10-06', '2026-10-05'])
        self.assertEqual(len(payload['history']), 30)
        self.assertEqual(sum(day['metadata']['available'] for day in payload['history']), 2)
        self.assertEqual(summarize_daily(payload['today'])['total_seconds'], 60)
        self.assertEqual(summarize_daily(payload['yesterday'])['total_seconds'], 0)
        self.dm.db.load_daily_work_reports.assert_called_once_with('2026-09-07', '2026-10-06')
        json.dumps(payload, allow_nan=False)

    def test_restart_preserves_prior_day_and_never_refetches_closed_audit(self):
        audit = self.directory / 'reports' / 'daily-audit-2026-10-05' / 'verified-submissions.json'
        audit.parent.mkdir(parents=True)
        saved = capture([evidence(day='2026-10-05')], day='2026-10-05')
        saved['rows'] = aggregate_observations(saved['tasks'])
        audit.write_text(json.dumps(saved), encoding='utf-8')
        before = audit.read_bytes()
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch', return_value=source([task()])) as fetch:
            first = self.dm.get_daily_report_data(force_refresh=True)
            restarted = self.manager().get_daily_report_data(force_refresh=True)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(first['yesterday']['rows'], restarted['yesterday']['rows'])
        self.assertEqual(before, audit.read_bytes())
        self.assertTrue((self.directory / 'reports' / 'daily-work' / '2026-10-05.json').exists())
        self.assertEqual(first['yesterday']['metadata']['capture_kind'], 'verified_audit_import')

    def test_repeat_current_capture_retains_tasks_moved_to_another_day(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
                   side_effect=[source([task('a'), task('b')]), source([task('b')])]):
            self.dm.get_todays_work_df('2026-10-06', True)
            result = self.dm.get_todays_work_df('2026-10-06', True)
        self.assertEqual(result.iloc[0]['Total Tasks'], 2)
        self.assertEqual(self.manager().get_todays_work_df('2026-10-06').iloc[0]['Total Tasks'], 2)

    def test_failed_today_is_unavailable_without_a_saved_capture(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch', side_effect=ConnectionError('Offline')):
            payload = self.dm.get_daily_report_data(force_refresh=True)
        self.assertFalse(payload['today']['metadata']['available'])
        self.assertIsNone(summarize_daily(payload['today'])['total_seconds'])
        self.assertEqual(payload['today']['metadata']['error'], 'Offline')
        self.assertFalse(any(day['metadata']['available'] for day in payload['history']))

    def test_failed_today_keeps_matching_capture_and_stale_error_metadata(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch', return_value=source([task()])):
            self.dm.get_daily_report_data(force_refresh=True)
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch', side_effect=ConnectionError('Offline')):
            payload = self.dm.get_daily_report_data(force_refresh=True)
        self.assertTrue(payload['today']['metadata']['available'])
        self.assertTrue(payload['today']['metadata']['is_snapshot'])
        self.assertEqual(payload['today']['metadata']['error'], 'Offline')
        self.assertEqual(summarize_daily(payload['today'])['total_seconds'], 60)

    def test_historical_report_and_empty_history_make_no_source_calls(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch') as fetch:
            payload = self.dm.get_daily_report_data('2026-10-04', True)
        fetch.assert_not_called()
        self.assertFalse(payload['today']['metadata']['available'])

    def test_yesterday_refresh_adds_late_submissions_to_partial_afternoon_capture(self):
        early = evidence('early', day='2026-10-05', seconds=10379)
        saved = merge_daily_reports(None, capture([early], day='2026-10-05'))
        self.dm._persist_daily_report(saved)
        late = task('late', submitted='2026-10-05T21:00:00+08:00', seconds=7657)
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
                   return_value=source([late])) as fetch:
            payload = self.dm.get_daily_report_data('2026-10-05', True)
        fetch.assert_called_once_with('2026-10-05')
        self.assertEqual(summarize_daily(payload['today'])['total_seconds'], 18036)
        self.assertEqual(payload['today']['metadata']['capture_kind'], 'day_end_reconciliation')
        stored = json.loads((self.directory / 'reports/daily-work/2026-10-05.json').read_text())
        self.assertEqual({row['task_id'] for row in stored['tasks']}, {'early', 'late'})
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
                   return_value=source([late])):
            repeated = self.manager().get_daily_report_data('2026-10-05', True)
        self.assertEqual(summarize_daily(repeated['today'])['total_seconds'], 18036)

    def test_failed_yesterday_reconciliation_marks_partial_capture_as_saved(self):
        saved = merge_daily_reports(None, capture([evidence(day='2026-10-05')], day='2026-10-05'))
        self.dm._persist_daily_report(saved)
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
                   side_effect=ConnectionError('Offline')):
            payload = self.dm.get_daily_report_data('2026-10-05', True)
        self.assertEqual(summarize_daily(payload['today'])['total_seconds'], 60)
        self.assertTrue(payload['today']['metadata']['is_snapshot'])
        self.assertEqual(payload['today']['metadata']['error'], 'Offline')

    def test_persistence_failure_preserves_valid_observation_with_warning(self):
        with patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch', return_value=source([task()])), \
             patch.object(self.dm, '_persist_daily_report', return_value=False):
            payload = self.dm.get_daily_report_data(force_refresh=True)
        self.assertTrue(payload['today']['metadata']['available'])
        self.assertIn('persistence_error', payload['today']['metadata'])
        self.assertEqual(summarize_daily(payload['today'])['total_seconds'], 60)


class MemoryCollection:
    """Mongo compare-and-swap fake, optionally injecting a concurrent writer."""
    def __init__(self):
        self.records = {}
        self.race = None

    def find_one(self, query):
        return copy.deepcopy(self.records.get(query['_id']))

    def find(self, query):
        dates = query['_id']
        return [copy.deepcopy(record) for day, record in self.records.items()
                if dates['$gte'] <= day <= dates['$lte']]

    def insert_one(self, record):
        if record['_id'] in self.records:
            raise DuplicateKeyError('date already inserted')
        self.records[record['_id']] = copy.deepcopy(record)

    def replace_one(self, query, record):
        if self.race:
            self.records[query['_id']] = self.race
            self.race = None
        current = self.records.get(query['_id'], {})
        matches = current.get('_revision') == query['_revision']
        if matches:
            self.records[query['_id']] = copy.deepcopy(record)
        return MagicMock(matched_count=int(matches))


class TestDailyReportPersistence(unittest.TestCase):
    def setUp(self):
        clock = patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06')
        clock.start()
        self.addCleanup(clock.stop)
        self.collection = MemoryCollection()
        self.db = DatabaseManager.__new__(DatabaseManager)
        self.db._connected = True
        self.db.mongo_db = {'daily_work_reports': self.collection}
        self.db.engine = None

    def test_mongo_idempotently_preserves_task_union_and_closed_date(self):
        self.assertTrue(self.db.save_daily_work_report(capture([evidence('a')])))
        self.assertTrue(self.db.save_daily_work_report(capture([evidence('b')])))
        self.assertTrue(self.db.save_daily_work_report(capture([evidence('b')])))
        record = self.db.load_daily_work_reports('2026-10-06', '2026-10-06')[0]
        self.assertEqual(record['rows'][0]['Total Tasks'], 2)
        closed_before = copy.deepcopy(record['rows'])
        self.assertTrue(self.db.save_daily_work_report(capture([], observed_on='2026-10-07')))
        self.assertEqual(self.db.load_daily_work_reports('2026-10-06', '2026-10-06')[0]['rows'], closed_before)

    def test_mongo_retry_merges_concurrent_capture(self):
        self.db.save_daily_work_report(capture([evidence('a')]))
        raced = merge_daily_reports(self.collection.records['2026-10-06'], capture([evidence('c')]))
        self.collection.race = {**raced, '_id': '2026-10-06', '_revision': 2}
        self.assertTrue(self.db.save_daily_work_report(capture([evidence('b')])))
        self.assertEqual(self.collection.records['2026-10-06']['rows'][0]['Total Tasks'], 3)

    def test_mongo_persists_next_day_reconciliation_without_erasing_earlier_tasks(self):
        self.db.save_daily_work_report(capture([evidence('early')]))
        late = capture([evidence('late', seconds=120)], observed_on='2026-10-07')
        late['metadata'].update(capture_kind='day_end_reconciliation', target_date='2026-10-06')
        with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-07'):
            self.assertTrue(self.db.save_daily_work_report(late))
        saved = self.db.load_daily_work_reports('2026-10-06', '2026-10-06')[0]
        self.assertEqual(saved['rows'][0]['Total Duration'], 180)
        self.assertEqual({task['task_id'] for task in saved['tasks']}, {'early', 'late'})

    def test_postgres_uses_date_lock_and_json_upsert(self):
        self.db.mongo_db = None
        self.db.engine = MagicMock()
        connection = self.db.engine.begin.return_value.__enter__.return_value
        connection.execute.return_value.scalar.return_value = None
        self.assertTrue(self.db.save_daily_work_report(capture([evidence()])))
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertTrue(any('pg_advisory_xact_lock' in statement for statement in statements))
        self.assertTrue(any('FOR UPDATE' in statement for statement in statements))
        self.assertTrue(any('ON CONFLICT' in statement for statement in statements))
        saved = json.loads(connection.execute.call_args.args[1]['payload'])
        self.assertEqual(saved['rows'][0]['Total Duration'], 60)

    def test_delayed_prior_date_writer_cannot_reopen_or_overwrite_closed_day(self):
        self.db.save_daily_work_report(capture([evidence('a')]))
        with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-07'):
            self.db.save_daily_work_report(capture([evidence('b')]))
        self.assertEqual(self.collection.records['2026-10-06']['rows'][0]['Total Tasks'], 1)

    def test_unavailable_report_is_never_persisted_as_empty_valid_day(self):
        self.assertFalse(self.db.save_daily_work_report(unavailable_day('2026-10-06')))
        self.assertEqual(self.collection.records, {})


if __name__ == '__main__':
    unittest.main()
