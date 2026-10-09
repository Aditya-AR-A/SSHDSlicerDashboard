"""Plots must not wait for unrelated captures or download full task evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

import dash
from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from tests.test_daily_report_page import application_namespace


def test_chart_dependencies_exclude_workflow_capture_and_slow_selection_writer():
    namespace, _ = application_namespace()
    callbacks = namespace['app'].callback_map
    for identity in ('pending-chart.figure', '..approval-trend-chart.figure...approval-trend-status.children..'):
        callback = callbacks[identity]
        assert 'workflow-sync-store' not in {item['id'] for item in callback['inputs']}
    selection = callbacks['selected-users-store.data']
    assert {item['id'] for item in selection['inputs']} == {'universal-legend', 'individual-chart'}
    assert not any('kpi-cards' in key and 'selected-users-store' in key for key in callbacks)


def test_line_load_does_not_run_live_workflow_or_daily_scans():
    namespace, manager = application_namespace()
    manager.get_dashboard_trend_data.return_value = {'history': []}
    with patch('dash.ctx', MagicMock(triggered_id='refresh-btn')):
        assert namespace['load_dashboard_trend']('2026-10-06', 1, 1, '/') == {'history': []}
    manager.get_dashboard_trend_data.assert_called_once_with('2026-10-06', force_refresh=True)
    manager.get_daily_report_data.assert_not_called()
    manager.get_workflow_data.assert_not_called()


def test_chart_projection_omits_audit_fields_and_preserves_remapping():
    database = DatabaseManager.__new__(DatabaseManager)
    collection = MagicMock()
    collection.with_options.return_value = collection
    database.mongo_db = {'daily_work_reports': collection}
    collection.find.return_value.batch_size.return_value = []
    assert database.load_daily_chart_reports('2026-10-01', '2026-10-06') == []
    query, fields = collection.find.call_args.args
    assert query['_id'] == {'$gte': '2026-10-01', '$lte': '2026-10-06'}
    assert fields['tasks.username'] == fields['tasks.bucket'] == fields['tasks.duration_seconds'] == 1
    assert 'tasks' not in fields and 'tasks.submitted_at' not in fields
    assert collection.find.call_args_list[0].args[1]['chart_rows'] == 1


def test_saved_line_reads_overlap_do_not_wait_for_daily_capture_and_remap_aliases():
    manager = DataManager.__new__(DataManager)
    manager.user_mapping = {'one': 'Person', 'two': 'Person'}
    manager.user_mapping_full = {}
    manager.db = MagicMock()
    day = {'date': '2026-10-06', 'metadata': {'available': True}, 'tasks': [
        {'username': 'one', 'user_id': 1, 'bucket': 'Fresh', 'duration_seconds': 3600},
        {'username': 'two', 'user_id': 2, 'bucket': 'Same-day Rework', 'duration_seconds': 1800}]}
    work_started, approvals_started, efficiency_started = Event(), Event(), Event()
    def work(*args):
        work_started.set()
        assert approvals_started.wait(1) and efficiency_started.wait(1)
        return [day]
    def approvals(*args):
        approvals_started.set()
        assert work_started.wait(1)
        return {'rows': []}
    def efficiency(*args, **kwargs):
        efficiency_started.set()
        assert kwargs == {'capture_live': False}
        assert work_started.wait(1)
        return []
    manager.db.load_daily_chart_reports.side_effect = work
    manager.get_approval_data = MagicMock(side_effect=approvals)
    manager.get_efficiency_history = MagicMock(side_effect=efficiency)
    manager.get_todays_work_df = MagicMock(side_effect=AssertionError('Live inventory scan'))
    with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.reporting.dashboard_trend.today_iso', return_value='2026-10-06'):
        # The existing slow daily capture owns its payload lock in another thread.
        from slicing_dashboard.data_manager import _DAILY_PAYLOAD_LOCK
        with ThreadPoolExecutor(max_workers=1) as pool:
            with _DAILY_PAYLOAD_LOCK:
                payload = pool.submit(manager.get_dashboard_trend_data, '2026-10-06').result(timeout=2)
        assert payload['today']['rows'][0]['Total Duration'] == 5400
        assert len(payload['today']['rows']) == 1
        payload['history'].clear()
        assert len(manager.get_dashboard_trend_data('2026-10-06')['history']) == 30
    manager.db.load_daily_chart_reports.assert_called_once()
    manager.get_todays_work_df.assert_not_called()


def test_compact_daily_rows_match_task_evidence_after_alias_split_and_exclusions():
    from slicing_dashboard.reporting.chart_projections import daily_chart_rows, remap_daily_chart
    from slicing_dashboard.reporting.dashboard_reports import aggregate_observations
    tasks = [{'username': account, 'bucket': bucket, 'duration_seconds': seconds}
             for account, bucket, seconds in [('a', 'Fresh', 3600), ('b', 'Same-day Rework', 1800),
                                               ('a', 'Old Rework', 900), ('excluded', 'Fresh', 500),
                                               ('Admin', 'Fresh', 300)]]
    compact = daily_chart_rows({'tasks': tasks})
    for aliases in ({'a': 'Person', 'b': 'Person', 'excluded': 'Exempt', 'Admin': 'Person'},
                    {'a': 'Person', 'b': 'Other', 'excluded': 'Exempt', 'Admin': 'Exempt'}):
        resolver = lambda uid, name: aliases.get(name, name)
        assert remap_daily_chart(compact, resolver) == aggregate_observations(tasks, resolver)


def test_compact_workflow_fields_preserve_completion_and_approval_totals():
    from copy import deepcopy
    from slicing_dashboard.reporting.chart_projections import workflow_chart
    from slicing_dashboard.reporting.approval_reports import approval_records
    event = {'id': 'approved-one', 'source': 'batch-review', 'action': 'Approved', 'active': True,
             'event_at': '2026-10-06T04:00:00Z', 'member': 'Person', 'stage': 'Leader',
             'batch_id': 'ab_one', 'cycle': 1, 'batch_video_seconds_at_capture': 3600,
             'raw': {'large_audit': 'preserved'}, 'provenance': 'Observed'}
    original = deepcopy(event)
    compact = workflow_chart('event', event)
    assert 'raw' not in compact and event == original
    assert approval_records([compact]) == approval_records([event])


def test_chart_replica_preference_never_changes_writer_database():
    from pymongo import ReadPreference
    from slicing_dashboard.processing.workflow_history import WorkflowStore
    primary, replica = MagicMock(), MagicMock()
    primary.with_options.return_value = replica
    store = WorkflowStore.__new__(WorkflowStore)
    store.mongo = primary
    reader = store.chart_reader()
    assert store.mongo is primary and reader.mongo is replica
    primary.with_options.assert_called_once_with(read_preference=ReadPreference.SECONDARY_PREFERRED)


def test_failed_chart_read_retains_memory_and_labels_local_evidence(tmp_path):
    import json
    from slicing_dashboard.reporting.dashboard_trend import work_records
    manager = DataManager.__new__(DataManager)
    manager.db = MagicMock()
    manager.db.load_daily_chart_reports.side_effect = RuntimeError('Unavailable')
    manager._daily_report_records = {'2026-10-05': {'date': '2026-10-05', 'rows': []}}
    directory = tmp_path / 'reports' / 'daily-work'
    directory.mkdir(parents=True)
    record = {'date': '2026-10-06', 'rows': [{'User': 'Person', 'Total Duration': 3600}],
              'metadata': {'available': True}}
    (directory / '2026-10-06.json').write_text(json.dumps(record))
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path):
        retained = work_records(manager, '2026-09-07', '2026-10-06')
    assert retained['2026-10-06']['rows'] == record['rows']
    assert all(day['metadata']['is_snapshot'] and day['metadata']['error'] == 'DailyChartReadFailed'
               for day in retained.values())
    assert 'metadata' not in manager._daily_report_records['2026-10-05']
