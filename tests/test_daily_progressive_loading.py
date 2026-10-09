"""Saved figures stay available while live verification runs separately."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

import dash
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.reporting.chart_projections import daily_chart_rows
from tests.test_daily_report_page import application_namespace, report_fixture, payload_text


def test_saved_report_does_not_wait_for_an_inflight_live_capture(tmp_path):
    manager = DataManager.__new__(DataManager)
    manager.user_mapping = {'account': 'Person'}
    manager.user_mapping_full = {}
    manager.db = MagicMock()
    manager.db.load_daily_chart_reports.return_value = [{
        'date': '2026-10-09', 'metadata': {'available': True},
        'chart_rows': daily_chart_rows({'tasks': [
            {'username': 'account', 'bucket': 'Fresh', 'duration_seconds': 60}]})}]
    started, release = Event(), Event()

    def capture(*args, **kwargs):
        started.set()
        assert release.wait(3), 'saved preparation waited on the live lock'
        return report_fixture()

    with patch('slicing_dashboard.config.DATA_DIR', tmp_path), \
         patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-09'), \
         patch('slicing_dashboard.reporting.dashboard_reports.get_daily_report_data', side_effect=capture):
        with ThreadPoolExecutor(max_workers=1) as pool:
            live = pool.submit(manager.get_daily_report_data, '2026-10-09', True)
            try:
                assert started.wait(2)
                saved = manager.get_saved_daily_report_data('2026-10-09')
                assert not live.done()
                assert saved['today']['rows'][0]['Total Duration'] == 60
                assert saved['saved_only'] and saved['refresh_pending']
                # Current mappings also invalidate the fast report cache.
                manager.user_mapping['account'] = 'Renamed'
                renamed = manager.get_saved_daily_report_data('2026-10-09')
                assert renamed['today']['rows'][0]['User'] == 'Renamed'
            finally:
                release.set()
            live.result()
    manager.db.load_daily_work_reports.assert_not_called()
    manager.db.save_daily_work_report.assert_not_called()


def test_first_paint_launches_live_capture_after_render_and_theme_does_not_repeat_it():
    namespace, manager = application_namespace()
    saved = {**report_fixture(), 'request_id': 'new', 'refresh_pending': True, 'saved_only': True}
    manager.get_saved_daily_report_data.return_value = saved
    render = namespace['render_daily_report']
    first = render(saved, 0, '/reports/daily')
    assert first[3] == saved
    assert 'checking the latest' in payload_text(first[0])
    manager.get_daily_report_data.assert_not_called()
    assert render(saved, 1, '/reports/daily', saved)[3] is dash.no_update
    # Live capture is triggered by the request returned with painted content,
    # rather than an input that blocks the saved renderer in Dash's graph.
    app = namespace['app']
    initial = next(value for key, value in app.callback_map.items() if 'daily-live-request.data' in key)
    assert 'daily-live-store' not in {entry['id'] for entry in initial['inputs']}
    assert app.callback_map['daily-live-store.data']['inputs'] == [
        {'id': 'daily-live-request', 'property': 'data'}]


def test_old_date_or_refresh_cannot_replace_new_saved_report_and_theme_retains_live_figures():
    namespace, _ = application_namespace()
    saved = {**report_fixture(), 'request_id': 'new', 'refresh_pending': True}
    live = {**report_fixture(), 'request_id': 'old', 'refresh_pending': False}
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['render_daily_live'](live, saved, 0, '/reports/daily')
    live['request_id'] = saved['request_id']
    live['report_date'] = '2026-10-05'
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['render_daily_live'](live, saved, 0, '/reports/daily')
    live['report_date'] = saved['report_date']
    live['live_refresh_error'] = True
    themed = namespace['render_daily_report'](saved, 1, '/reports/daily', saved, live)
    assert 'Latest submissions could not be verified' in payload_text(themed[0])
    assert 'checking the latest' not in payload_text(themed[0])
    assert themed[3] is dash.no_update


def test_live_failure_keeps_the_saved_report_with_an_explicit_notice():
    namespace, manager = application_namespace()
    saved = {**report_fixture(), 'request_id': 'new', 'refresh_pending': True}
    manager.get_daily_report_data.side_effect = ConnectionError('Source unavailable')
    failed = namespace['load_daily_live'](saved, '/reports/daily')
    assert failed['history'] == saved['history']
    assert failed['live_refresh_error'] and not failed['refresh_pending']
    assert 'Latest submissions could not be verified' in payload_text(
        namespace['render_daily_live'](failed, saved, 0, '/reports/daily')[0])


def test_immutable_historical_report_never_launches_a_live_scan():
    namespace, manager = application_namespace()
    saved = {**report_fixture(), 'request_id': 'history', 'refresh_pending': False}
    assert namespace['render_daily_report'](saved, 0, '/reports/daily')[3] is dash.no_update
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['load_daily_live'](saved, '/reports/daily')
    manager.get_daily_report_data.assert_not_called()
