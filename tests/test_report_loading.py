"""Cross-page capture budgets and coverage-safe report additions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import dash
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.plots.report_insights import build_activity_calendar, build_day_comparison
from tests.test_daily_report_page import application_namespace, report_fixture
from tests.test_workflow_history import service, completed, evidence, review


def test_reports_share_recent_capture_expire_refresh_and_mapping_edits():
    manager = DataManager.__new__(DataManager)
    manager.user_mapping_full = {'account': {'mapped_user': 'Riya'}}
    payload = report_fixture()
    clock = MagicMock(return_value=100)
    with patch('slicing_dashboard.data_manager.perf_counter', clock), \
         patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.reporting.dashboard_reports.get_daily_report_data', return_value=payload) as source:
        first = manager.get_daily_report_data('2026-10-06')
        first['users'].append('Do not retain caller edits')
        assert manager.get_daily_report_data('2026-10-06')['users'] == ['Priya', 'Riya']
        source.assert_called_once_with(manager, '2026-10-06', force_refresh=True)
        manager.get_daily_report_data('2026-10-06', force_refresh=True)
        assert source.call_count == 2
        clock.return_value = 161
        manager.get_daily_report_data('2026-10-06')
        assert source.call_count == 3
        manager.user_mapping_full['account']['mapped_user'] = 'Priya'
        manager.get_daily_report_data('2026-10-06')
        assert source.call_count == 4


def test_simultaneous_route_loads_share_one_capture():
    manager = DataManager.__new__(DataManager)
    started, release = Event(), Event()
    def capture(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return report_fixture()
    with patch('slicing_dashboard.reporting.dashboard_reports.get_daily_report_data', side_effect=capture) as source:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(manager.get_daily_report_data, '2026-10-06')
            assert started.wait(2)
            second = pool.submit(manager.get_daily_report_data, '2026-10-06')
            release.set()
            assert first.result() == second.result()
        assert source.call_count == 1


@pytest.mark.parametrize('age,unverified,force', [(10, False, False), (90, False, True), (10, True, True)])
def test_cold_worker_reuses_only_recent_verified_durable_capture(age, unverified, force):
    manager = DataManager.__new__(DataManager)
    manager.db = MagicMock()
    manager._load_daily_report_records = MagicMock(return_value={'2026-10-06': {'metadata': {
        'available': True, 'is_snapshot': unverified,
        'captured_at': (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}}})
    with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.reporting.dashboard_reports.get_daily_report_data', return_value=report_fixture()) as source:
        manager.get_daily_report_data('2026-10-06')
        assert source.call_args.kwargs['force_refresh'] is force


def test_route_entry_uses_cache_but_explicit_refresh_forces_live_data():
    namespace, manager = application_namespace()
    for name, path, method in [('load_daily_report', '/reports/daily', 'get_daily_report_data'),
                               ('load_user_report', '/reports/user', 'get_user_report_data')]:
        for trigger, force in [('report-location.pathname', False), ('refresh-btn.n_clicks', True)]:
            with patch('dash.callback_context', MagicMock(triggered=[{'prop_id': trigger}])):
                namespace[name](path, 1, 0, '2026-10-06')
            assert getattr(manager, method).call_args.kwargs['force_refresh'] is force


def test_immediate_legend_render_is_pure_and_route_scoped():
    namespace, manager = application_namespace()
    for clicks in (0, 1):
        figure = namespace['render_dashboard_legend'](['Riya'], clicks, '/')
        assert {trace.name for trace in figure.data} == {'Priya', 'Riya'}
        assert next(trace for trace in figure.data if trace.name == 'Priya').visible == 'legendonly'
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['render_dashboard_legend'](['Riya'], 0, '/reports/daily')
    manager.fetch_dashboard_data.assert_not_called()
    callback = next(value for key, value in namespace['app'].callback_map.items()
                    if 'kpi-cards.children' in key)
    outputs = {output.component_id for output in callback['output']}
    assert 'universal-legend' not in outputs and 'dashboard-trend-store' not in outputs


def test_failed_workflow_poll_retains_last_known_approval_evidence():
    namespace, manager = application_namespace()
    manager.get_workflow_data.side_effect = ConnectionError('Do not expose source details')
    previous = {'approvals': {'rows': [{'date': '2026-10-06', 'stage': 'Leader', 'seconds': 3600}]},
                'inbox': {'unread': 3, 'rows': []}}
    result = namespace['load_workflow_data']('/', 1, 0, 0, 0, previous)
    assert result['approvals'] == previous['approvals']
    assert result['inbox']['unread'] == 3
    assert 'error' in result and 'Do not expose' not in result['error']


def test_comparison_preserves_unknown_prior_day_and_captured_zero_users():
    payload = report_fixture(rows=[{'User': 'Riya', 'Total Duration': 3600, 'Total Tasks': 1}])
    figure = build_day_comparison(payload['today'], payload['yesterday'], payload['users'])
    assert list(figure.data[0].x) == [0, 1]
    assert all(value is None for value in figure.data[1].x)
    assert 'Previous day unavailable' in figure.layout.annotations[0].text
    assert figure.data[0].customdata[-1][1] == '01:00:00'


def test_calendar_distinguishes_observed_zero_and_missing_captures():
    day = report_fixture()['today']
    figure, count = build_activity_calendar({'2026-10-06': day}, 'Riya', '2026-10-07')
    assert count == 1
    values = [value for row in figure.data[1].z for value in row if value is not None]
    unknown = [value for row in figure.data[0].z for value in row if value is not None]
    assert values == [0]
    assert len(unknown) == 89
    figure.to_json()


def test_unchanged_workflow_poll_skips_projection_writes_but_new_evidence_reprojects(tmp_path):
    history, _ = service(tmp_path)
    history.manager._batches_master_cache = {'ab_example': completed()}
    history.sync(force=True)
    with patch.object(history.store, 'save_many', wraps=history.store.save_many) as writes:
        history.capture_batches()
        assert not writes.called
        row = evidence('batch-review', review('leader', status='batch_pending_auditor_review')['raw'],
                       instance=history.instance)
        history.store.save(row['id'], 'observation', row)
        history.capture_batches()
        assert 'event' in [call.args[0] for call in writes.call_args_list]


def test_failed_projection_is_retried(tmp_path):
    history, _ = service(tmp_path)
    history.manager._batches_master_cache = {'ab_example': completed()}
    original = history.store.save_many
    def fail(kind, *args, **kwargs):
        if kind == 'notification':
            raise ConnectionError('Fixture write unavailable')
        return original(kind, *args, **kwargs)
    with patch.object(history.store, 'save_many', side_effect=fail):
        with pytest.raises(ConnectionError):
            history.capture_batches()
    assert not hasattr(history, '_projection_fingerprint')
    history.capture_batches()
    assert history.notifications()['total'] > 0


def test_chart_batch_capture_skips_busy_collector_and_can_retry(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from slicing_dashboard.processing.workflow_history import LOCK

    history, _ = service(tmp_path)
    history.manager._batches_master_cache = {'ab_example': completed()}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with LOCK:
            capture = pool.submit(history.capture_batches, wait=False)
            assert capture.result(timeout=1) is False
        assert history.capture_batches(wait=False) is True
    assert history.notifications()['total'] > 0
