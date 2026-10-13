"""Review stages, tooltip attribution and date-scoped batch inventory regressions."""
from unittest.mock import MagicMock, patch
from contextlib import nullcontext
from threading import Event

import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.processing.pending_review import fetch_items, pending_frame
from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart
from slicing_dashboard.reporting.dashboard_reports import aggregate_observations, prepare_daily_work_chart_rows
from slicing_dashboard.reporting.chart_projections import daily_chart_rows, remap_daily_chart
from slicing_dashboard.reporting.user_batches import get_user_batches_data, worked_batch_ids


def test_review_uses_current_task_statuses_instead_of_stale_efficiency_stages():
    scraper = MagicMock(is_authenticated=True)
    scraper._base_url = 'https://source.test'
    queues = {
        'slice_submitted': [{'id': 'new', 'slicer_id': 1, 'slicer': 'new', 'duration_seconds': 7200}],
        'slice_pending_auditor_review': [{'id': 'old', 'slicer_id': 2, 'slicer': 'old', 'duration_seconds': 5400}],
        'slice_pending_admin_review': [],
    }
    def request(url, **kwargs):
        assert url.endswith('/api/slice/tasks'), 'stale efficiency endpoint must not supply chart quantities'
        status = kwargs['params']['status']
        response = MagicMock()
        response.json.return_value = {'data': [{**task, 'status': status} for task in queues[status]],
                                      'meta': {'has_more': False}}
        return response
    scraper._client.get.side_effect = request
    manager = DataManager.__new__(DataManager)
    manager.scraper = scraper
    manager.user_mapping = {'new': 'Aditya', 'old': 'Aditya', 'unused': 'Aditya'}
    frame = pending_frame(manager, True).set_index('Stage')
    assert frame.loc['Pending Leader', 'Duration'] == 7200
    assert frame.loc['Pending Auditor', 'Duration'] == 5400
    assert frame.loc['Pending Leader', 'IDs'] == 'new'
    assert frame.loc['Pending Auditor', 'IDs'] == 'old'
    assert frame.loc['Pending Admin', 'IDs'] == ''
    assert scraper._client.get.call_count == 3


def test_task_moving_between_two_review_queues_requires_retry():
    task = {'id': 'moving', 'slicer_id': 1, 'slicer': 'account', 'duration_seconds': 60}
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', return_value=[task]):
        with pytest.raises(ValueError, match='multiple review stages'):
            fetch_items(MagicMock(is_authenticated=True), '2026-10-09')


def test_each_daily_stack_lists_only_contributing_accounts_with_saved_projection_parity():
    tasks = [{'username': account, 'bucket': bucket, 'duration_seconds': 60}
             for account, bucket in [('fresh', 'Fresh'), ('same', 'Same-day Rework'), ('old', 'Old Rework')]]
    resolver = lambda uid, name: 'Person'
    rows = aggregate_observations(tasks, resolver)
    assert remap_daily_chart(daily_chart_rows({'tasks': tasks}), resolver) == rows
    day = {'rows': rows, 'metadata': {'available': True}}
    chart = build_error_rework_chart(prepare_daily_work_chart_rows(day), 'Today', True)
    assert {trace.name: trace.customdata[0][2] for trace in chart.data if trace.customdata is not None} == {
        'Fresh Work': 'fresh', 'Same-day Rework': 'same', 'Old Rework (excluded)': 'old'}


def test_legacy_person_summary_does_not_invent_bucket_account_attribution():
    rows = [{'User': 'Person', 'RawID': 'a,b', 'New Videos (First Time)': 60, 'Same-day Rework': 30}]
    chart = build_error_rework_chart(prepare_daily_work_chart_rows({'rows': rows, 'metadata': {'available': True}}), 'Today', True)
    assert chart.data[0].customdata[0][2] == 'Unavailable in saved capture'


def test_worked_batch_link_uses_unique_complete_account_totals_not_assignment_date():
    tasks = [{'username': 'old', 'slice_batch': 2}]
    inventory = [{'slicer': 'old', 'slice_batch': 2, 'duration_seconds': 60},
                 {'slicer': 'old', 'slice_batch': 2, 'duration_seconds': 120}]
    batches = [{'batch_id': 'previous-day', 'username': 'old', 'task_count': 2, 'total_duration_seconds': 180},
               {'batch_id': 'unrelated', 'username': 'old', 'task_count': 3, 'total_duration_seconds': 180}]
    assert worked_batch_ids(tasks, inventory, batches) == ({'previous-day'}, [])
    batches.append({**batches[0], 'batch_id': 'ambiguous'})
    assert worked_batch_ids(tasks, inventory, batches)[0] == set()
    assert worked_batch_ids([{'username': 'old', 'slice_batch': 2, 'batch_id': 'explicit'}], [],
                            [{'batch_id': 'explicit'}]) == ({'explicit'}, [])
    ids, unresolved = worked_batch_ids([{'username': 'old', 'batch_id': 'missing'}], [], [])
    assert unresolved == [{'username': 'old', 'batch_id': 'missing'}]


@pytest.mark.parametrize('changing_offset', [False, True])
def test_batch_capture_contains_two_assigned_batches_plus_older_worked_batch_without_chart_scans(tmp_path, changing_offset):
    manager = DataManager.__new__(DataManager)
    manager.scraper = MagicMock(is_authenticated=True)
    manager.scraper._users = {1: {}, 2: {}}
    manager.scraper._get_username.side_effect = lambda uid: {1: 'new', 2: 'old'}[uid]
    manager.db = MagicMock()
    manager.db.load_daily_work_reports.return_value = [{'tasks': [{'task_id': 't', 'username': 'old', 'slice_batch': 2}]}]
    manager._batches_master_cache = {}
    assigned = [{'batch_id': 'one', 'assignee_id': 1, 'task_count': 2, 'total_duration_seconds': 120},
                {'batch_id': 'two', 'assignee_id': 1, 'task_count': 3, 'total_duration_seconds': 180}]
    older = {'batch_id': 'old', 'assignee_id': 2, 'task_count': 1, 'total_duration_seconds': 60}
    responses = [
        {'breakdowns': {'batches': assigned+[older]}, 'metrics': {'batch_count': 3}, 'scope': {'group_id': 803}},
        {'breakdowns': {'batches': assigned}, 'metrics': {'batch_count': 2}},
        {'items': [{'id': 't', 'slicer': 'old', 'slice_batch': 2, 'slice_submitted_at': None, 'duration_seconds': 60}],
         'meta': {'has_more': False}},
    ]
    if changing_offset:
        # An assignment arriving during pagination changes the declared total.
        # The partial offset capture must be discarded before batch matching.
        responses.insert(2, {**responses[2], 'total': 2,
                            'meta': {'has_more': False, 'total_capped': False, 'max_offset_rows': 10000,
                                     'page': 1, 'page_size': 500}})
    with patch('slicing_dashboard.reporting.user_batches._validate_date'), \
         patch('slicing_dashboard.config.DATA_DIR', tmp_path), \
         patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.request', side_effect=responses) as source:
        capture = get_user_batches_data(manager, '2026-10-09')
        assert capture['available'], capture
        assert {row['batch_id']: row['report_scope'] for row in capture['batches']} == {
            'one': 'Assigned', 'two': 'Assigned', 'old': 'Worked'}
        assert get_user_batches_data(manager, '2026-10-09') == capture
        assert source.call_count == (4 if changing_offset else 3)


def test_daily_history_read_overlaps_live_capture_and_never_overwrites_new_evidence():
    from slicing_dashboard.reporting.dashboard_reports import get_daily_report_data
    history_started, live_done = Event(), Event()
    manager = MagicMock()
    manager.refresh_scope.side_effect = lambda: nullcontext()
    manager.user_mapping = {'account': 'Person'}
    manager.user_mapping_full = {}
    manager._snapshot_payload = {}
    manager._get_reporting_name = lambda uid, name: 'Person'
    manager._daily_report_records = {'2026-10-07': {
        'date': '2026-10-07', 'rows': [{'User': 'Person', 'New Videos (First Time)': 30}],
        'metadata': {'available': True}}}
    manager._load_daily_report_records.return_value = {}

    def history(*args):
        history_started.set()
        assert live_done.wait(3), 'historical reads must overlap live capture'
        return {day: {'date': day, 'rows': [{'User': 'Person', 'New Videos (First Time)': seconds}],
                     'metadata': {'available': True}}
                for day, seconds in [('2026-10-07', 120), ('2026-10-09', 1)]}

    def live(day, **kwargs):
        assert history_started.wait(3)
        frame = __import__('pandas').DataFrame()
        manager._daily_report_records[day] = {'date': day,
            'tasks': [{'username': 'account', 'bucket': 'Fresh', 'duration_seconds': 60}],
            'metadata': {'available': True}}
        live_done.set()
        return frame

    manager.get_todays_work_df.side_effect = live
    with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-09'), \
         patch('slicing_dashboard.reporting.dashboard_trend.work_records', side_effect=history):
        report = get_daily_report_data(manager, '2026-10-09')
    assert report['today']['rows'][0]['Total Duration'] == 60
    assert next(day for day in report['history'] if day['date'] == '2026-10-07')['rows'][0]['Total Duration'] == 120
    manager._persist_daily_report.assert_not_called()


def test_batch_callback_reapplies_mapping_and_rejects_late_capture_for_another_date():
    from tests.test_daily_report_page import application_namespace, payload_text
    namespace, manager = application_namespace()
    manager._get_reporting_name.side_effect = lambda uid, name: 'Aditya' if name == 'alias' else name
    data = {'report_date': '2026-10-09'}
    capture = {'report_date': '2026-10-08', 'available': True,
               'batches': [{'batch_id': 'wrong-date', 'username': 'alias'}]}
    render = namespace['render_user_batches']
    assert 'wrong-date' not in payload_text(render(capture, 'Aditya', data, 0, '/reports/user'))
    capture['report_date'] = data['report_date']
    capture['batches'][0]['batch_id'] = 'matched-date'
    assert 'matched-date' in payload_text(render(capture, 'Aditya', data, 0, '/reports/user'))
    manager._get_reporting_name.side_effect = lambda uid, name: 'Other person'
    assert 'matched-date' not in payload_text(render(capture, 'Aditya', data, 0, '/reports/user'))
    dependencies = namespace['app'].callback_map['user-batches-store.data']['inputs']
    assert 'user-report-person' not in {dependency['id'] for dependency in dependencies}
