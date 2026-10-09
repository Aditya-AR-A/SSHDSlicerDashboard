"""Historical status recovery keeps dates, scopes, zeros and event semantics."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.processing.workflow_history import WorkflowStore
from slicing_dashboard.reporting.efficiency_history import EfficiencyHistory, FIELDS
from slicing_dashboard.reporting.approval_reports import prepare_approval_trend
from slicing_dashboard.plots.approval_trend_chart import build_approval_trend_chart
from tests.test_daily_report_page import application_namespace, report_fixture


def item(uid=1, username='worker', completed=3600):
    return {'user_id': uid, 'username': username, **{field: 0 for field in FIELDS},
            'completed_count': 1 if completed else 0, 'completed_duration_seconds': completed}


@pytest.fixture
def history(tmp_path):
    manager = SimpleNamespace(settings=SimpleNamespace(efficiency_leader_id=803),
        scraper=SimpleNamespace(_base_url='https://source.example', is_authenticated=True, _client=MagicMock()),
        db=None, _get_reporting_name=lambda uid, name: 'Priya' if name == 'worker' else name)
    return EfficiencyHistory(manager, WorkflowStore(path=tmp_path / 'history.sqlite3'))


def response(rows, total=None, day='2026-10-06'):
    result = MagicMock()
    result.json.return_value = {'items': rows, 'total': len(rows) if total is None else total,
                                'date_range': {'start_date': day, 'end_date': day}}
    return result


def test_post_pagination_persists_every_account_and_remaps_on_read(history):
    history.manager.scraper._client.post.side_effect = [response([item()], 2), response([item(2, 'worker', 1800)], 2)]
    record = history.capture('2026-10-06')
    assert len(record['items']) == 2
    calls = history.manager.scraper._client.post.call_args_list
    assert [call.kwargs['json']['page'] for call in calls] == [1, 2]
    assert all(call.kwargs['json']['leader_id'] == 803 for call in calls)
    assert all(call.kwargs['json']['role'] == 2 for call in calls)
    assert history.read('2026-10-06', '2026-10-06')[0]['rows'][0]['completed_duration_seconds'] == 5400
    history.manager._get_reporting_name = lambda uid, name: 'Rajni'
    assert history.read('2026-10-06', '2026-10-06')[0]['rows'][0]['User'] == 'Rajni'


@pytest.mark.parametrize('bad', ['repeated', 'truncated', 'wrong_date', 'negative'])
def test_bad_refresh_cannot_replace_retained_capture(history, bad):
    history.manager.scraper._client.post.return_value = response([item()])
    saved = history.capture('2026-10-06')
    payload = {'repeated': response([item(), item()]), 'truncated': response([], 1),
               'wrong_date': response([item()], day='2026-10-07'),
               'negative': response([item(completed=-1)])}[bad]
    history.manager.scraper._client.post.return_value = payload
    with pytest.raises(ValueError):
        history.capture('2026-10-06', force=True)
    assert history.store.get(history.key('2026-10-06')) == saved


def test_completion_is_visible_as_status_without_creating_approval_hours(history):
    history.manager.scraper._client.post.return_value = response([item()])
    history.capture('2026-10-06')
    data = report_fixture()
    data['efficiency_history'] = history.read('2026-10-05', '2026-10-06')
    report = prepare_approval_trend(data, ['Priya'])
    chart = build_approval_trend_chart(report)
    assert chart.data[-1].name.replace('<br>', ' ') == 'Completed Video (source date)'
    assert chart.data[-1].y[-1] == 1
    assert chart.data[-1].y[-2] is None
    assert all(value is None for trace in chart.data[1:4] for value in trace.y)
    assert prepare_approval_trend(data, ['Rajni'])['rows'][-1]['completed_seconds'] == 0
    assert 'Asia/Shanghai' in report['approval_status']


def test_saved_approvals_render_on_first_load_even_if_workflow_scan_failed():
    namespace, _ = application_namespace()
    data = report_fixture()
    data['approvals'] = {'rows': [{'date': '2026-10-06', 'member': 'Priya', 'stage': 'Leader', 'seconds': 3600}],
                         'complete': False}
    chart, note = namespace['render_dashboard_trend'](data, None, 0, '/', {'error': 'ScanTimeout'})
    assert chart.data[1].y[-1] == 1
    assert 'Saved approvals' in note


def test_daily_summary_survives_task_evidence_download_timeout():
    database = DatabaseManager.__new__(DatabaseManager)
    database.is_connected = lambda: True
    summary = {'_id': '2026-10-06', 'date': '2026-10-06',
               'rows': [{'User': 'Priya', 'Total Duration': 3600}], 'metadata': {'available': True}}
    cursor = MagicMock()
    cursor.batch_size.return_value = cursor
    cursor.__iter__.side_effect = TimeoutError('Large evidence read stalled')
    collection = MagicMock()
    collection.find.side_effect = [[summary], cursor]
    database.mongo_db = {'daily_work_reports': collection}
    database.engine = None
    result = database.load_daily_work_reports('2026-10-01', '2026-10-08')
    assert result[0]['date'] == '2026-10-06'
    assert result[0]['rows'][0]['Total Duration'] == 3600
    assert result[0]['metadata']['is_snapshot']
    assert result[0]['metadata']['error'] == 'DailyEvidenceReadFailed'


def test_approval_read_never_triggers_workflow_scan():
    manager = DataManager.__new__(DataManager)
    store = MagicMock()
    store.chart_reader.return_value = store
    store.approval_events.return_value = []
    store.records.return_value = []
    history = SimpleNamespace(store=store, instance='scope', sync=MagicMock())
    manager._workflow_history_service = history
    manager._batches_master_cache = {}
    manager.get_approval_data('2026-10-01', '2026-10-08')
    history.sync.assert_not_called()
    store.approval_events.assert_called_once_with('scope', '2026-10-01', '2026-10-08', chart_only=True)


def test_compact_completion_reader_matches_full_history_and_remaps(history):
    history.manager.scraper._client.post.return_value = response([item(), item(2, 'alias', 1800)])
    history.capture('2026-10-06')
    full = history.read('2026-10-06', '2026-10-06')
    compact = history.read_completed('2026-10-06', '2026-10-06')
    assert {row['User']: row['completed_duration_seconds'] for row in compact[0]['rows']} == {
        row['User']: row['completed_duration_seconds'] for row in full[0]['rows']}
