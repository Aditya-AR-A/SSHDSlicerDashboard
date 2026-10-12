"""First paint, independent sources, durable cache scope and failure retention."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dash
import pandas as pd
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.pages.dashboard_panels import choose_packet, render_panel
from slicing_dashboard.reporting.dashboard_panels import (
    KINDS, capture_panel, saved_panel, selected_summary, SUM_FIELDS, attach_panels,
)
from tests.test_daily_report_page import application_namespace
from tests.test_refresh_performance import manager as real_manager


def fixture_manager():
    manager = MagicMock()
    manager.scraper._base_url = 'https://fixture.invalid'
    manager.settings = SimpleNamespace(admin_username='test')
    manager.user_mapping = {'account': 'Riya'}
    manager.user_mapping_full = {}
    manager.exempt_ids = set()
    manager._snapshot_payload = {}
    manager.refresh_scope.side_effect = lambda **kwargs: nullcontext()
    manager.get_user_breakdown_df.return_value = pd.DataFrame([{'User': 'Riya', 'Completed Duration': 3600}])
    manager.get_batch_rework_ratio_df.return_value = pd.DataFrame([{'User': 'Riya', 'Total Batches': 1, 'No Rework': 1}])
    frame = pd.DataFrame(columns=['User', 'Stage', 'Duration', 'Count', 'IDs'])
    manager.get_pending_review_df.return_value = frame
    manager.get_detailed_pending_assigned_df.return_value = frame
    manager.fetch_dashboard_data.return_value = {'breakdowns': {'slice_funnel': []}}
    manager.get_summary_kpis.return_value = {**{key: 1 for key in SUM_FIELDS},
        'approved_pct': 0, 'error_pct': 0, 'rework_pct': 0, 'completed_pct': 0,
        'assignable_duration': 0, 'assignable_count': 0}
    return manager


def request_for(identity='one'):
    return {'id': identity, 'start': '2026-10-01', 'end': '2026-10-08', 'force': False, 'pending_force': False}


def test_saved_request_and_render_do_no_source_reads_and_have_no_live_input():
    namespace, manager = application_namespace()
    source = fixture_manager()
    packet = capture_panel(source, 'completion', '2026-10-01', '2026-10-08')
    manager._snapshot_payload = source._snapshot_payload
    manager.scraper._base_url = source.scraper._base_url
    manager.settings = source.settings
    manager.user_mapping = source.user_mapping
    manager.user_mapping_full = {}
    manager.exempt_ids = set()
    callbacks = namespace['app'].callback_map
    request_callback = next(value for key, value in callbacks.items() if 'dashboard-panel-request.data' in key)
    with patch('dash.ctx', MagicMock(triggered_id='report-location')):
        request, _, saved, *_ = request_callback['callback'].__wrapped__(
            '2026-10-01', '2026-10-08', 0, 0, '/', 0, 0)
    assert saved['captured_at'] == packet['captured_at']
    paint = next(value for key, value in callbacks.items() if 'individual-chart.figure' in key and '@' not in key)
    assert not any(item['id'].startswith('dashboard-live-') for item in paint['inputs'])
    figure, status, ready, _ = paint['callback'].__wrapped__(request, saved, ['Riya'], 0, None, '/', None)
    assert ready['request_id'] == request['id']
    assert len(figure.data) == 1 and 'Checking latest data' in status
    manager.fetch_dashboard_data.assert_not_called()
    manager.get_user_breakdown_df.assert_not_called()
    manager._save_snapshot.assert_not_called()


def test_range_capture_waits_for_first_paint_and_selection_does_not_recapture():
    namespace, manager = application_namespace()
    callbacks = namespace['app'].callback_map
    capture = callbacks['dashboard-live-completion.data']
    assert capture['inputs'] == [{'id': 'dashboard-ready-completion', 'property': 'data'}]
    request = request_for('new-range')
    with pytest.raises(dash.exceptions.PreventUpdate):
        capture['callback'].__wrapped__({'request_id': 'old-range'}, request)
    manager.get_user_breakdown_df.assert_not_called()
    paint = next(value for key, value in callbacks.items() if 'individual-chart.figure' in key and '@' not in key)
    _, _, ready, painted = paint['callback'].__wrapped__(request, None, ['Riya'], 0, None, '/', None)
    assert ready['request_id'] == painted['request_id'] == 'new-range'
    _, _, repeat, _ = paint['callback'].__wrapped__(request, None, [], 1, None, '/', ready)
    assert repeat is dash.no_update


def test_live_result_is_reapplied_after_a_late_selection_paint():
    namespace, manager = application_namespace()
    callbacks = namespace['app'].callback_map
    live = next(value for key, value in callbacks.items() if 'individual-chart.figure@' in key)
    assert {'id': 'dashboard-painted-completion', 'property': 'data'} in live['inputs']
    packet = {**capture_panel(fixture_manager(), 'completion', '2026-10-01', '2026-10-08'), 'request_id': 'one'}
    figure, status = live['callback'].__wrapped__(packet, {'request_id': 'one'}, request_for(), None, ['Riya'], 0, '/')
    assert len(figure.data) == 1 and status.startswith('Captured ')


def test_completion_finishes_while_rework_is_still_reading():
    manager = fixture_manager()
    started, release = Event(), Event()
    def slow(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return pd.DataFrame(columns=['User', 'Total Batches'])
    manager.get_batch_rework_ratio_df.side_effect = slow
    with ThreadPoolExecutor(max_workers=2) as pool:
        rework = pool.submit(capture_panel, manager, 'rework', '2026-10-01', '2026-10-08')
        try:
            assert started.wait(1)
            completion = pool.submit(capture_panel, manager, 'completion', '2026-10-01', '2026-10-08').result(timeout=1)
            assert completion['available'] and not rework.done()
        finally:
            release.set()
        assert rework.result()['available']


def test_panel_scope_accounts_mappings_version_and_current_day_are_isolated():
    manager = fixture_manager()
    with patch('slicing_dashboard.reporting.dashboard_panels.today_iso', return_value='2026-10-08'):
        capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')
        capture_panel(manager, 'pending', '2026-10-01', '2026-10-08')
        assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08')
        assert saved_panel(manager, 'completion', '2026-09-01', '2026-09-08') is None
        manager.user_mapping['account'] = 'Priya'
        assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') is None
        manager.user_mapping['account'] = 'Riya'
        manager.settings.admin_username = 'other'
        assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') is None
        manager.settings.admin_username = 'test'
        with patch('slicing_dashboard.reporting.dashboard_panels.VERSION', 2):
            assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') is None
    with patch('slicing_dashboard.reporting.dashboard_panels.today_iso', return_value='2026-10-09'):
        assert saved_panel(manager, 'pending', '2026-10-01', '2026-10-08') is None


def test_failure_retains_last_good_chart_and_timestamp_without_claiming_live():
    manager = fixture_manager()
    packet = capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')
    manager.get_user_breakdown_df.return_value.attrs['chart_error'] = 'Unavailable'
    failed = capture_panel(manager, 'completion', '2026-10-01', '2026-10-08', True)
    assert not failed['available']
    assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') == packet
    selected, note = choose_packet(request_for(), {**packet, 'request_id': 'one'}, {**failed, 'request_id': 'one'})
    assert selected['captured_at'] == packet['captured_at']
    assert 'Latest check failed' in note
    # A delayed response for another date request cannot replace this page's data.
    assert choose_packet(request_for('two'), {**packet, 'request_id': 'one'}, {**packet, 'request_id': 'one'})[0] is None


def test_selected_summaries_recompute_percentages_and_keep_global_pool():
    values = {key: 0 for key in SUM_FIELDS}
    data = {'all': {**values, 'assignable_duration': 500}, 'users': {
        'Riya': {**values, 'total_approved_duration': 120, 'prev_approved_duration': 60},
        'Priya': {**values, 'total_approved_duration': 180, 'prev_approved_duration': 240}}}
    result = selected_summary(data, ['Riya', 'Priya'], ['Riya', 'Priya', 'Other'])
    assert result['approved_pct'] == 0 and result['total_approved_duration'] == 300
    assert result['assignable_duration'] == 500
    assert selected_summary(data, ['Riya'], ['Riya', 'Priya'])['approved_pct'] == 100
    assert selected_summary(data, [], ['Riya'])['total_approved_duration'] == 0


def test_durable_snapshot_can_seed_a_new_worker_without_live_reads(tmp_path):
    source = fixture_manager()
    for kind in KINDS:
        assert capture_panel(source, kind, '2026-10-01', '2026-10-08')['available']
    writer = real_manager(tmp_path)
    writer._snapshot_payload = deepcopy(source._snapshot_payload)
    writer._save_snapshot()
    restarted = fixture_manager()
    restarted._snapshot_payload = json.loads((tmp_path / 'snapshot.json').read_text())
    assert saved_panel(restarted, 'completion', '2026-10-01', '2026-10-08')['data']['records'][0]['Completed Duration'] == 3600
    restarted.get_user_breakdown_df.assert_not_called()


def test_interactive_capture_scope_does_not_flush_full_snapshot(tmp_path):
    manager = real_manager(tmp_path)
    manager._save_snapshot = MagicMock(wraps=manager._save_snapshot)
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path):
        with manager.refresh_scope(persist_snapshot=False):
            manager._save_snapshot()
    manager.db.save_dashboard_snapshot.assert_not_called()
    assert not (tmp_path / 'snapshot.json').exists()
    assert manager.last_refresh_profile['snapshot_persistence'] == {'attempted': False}


def test_schedule_prepares_all_panels_and_retains_successes_on_partial_failure():
    manager = fixture_manager()
    manager.get_pending_review_df.return_value.attrs['pending_error'] = 'Failed'
    with pytest.raises(ValueError, match='pending'):
        DataManager.prepare_dashboard_panels(manager, '2026-10-01', '2026-10-08')
    assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08')['available']
    assert saved_panel(manager, 'pending', '2026-10-01', '2026-10-08') is None
    manager._save_snapshot.assert_called_once()


def test_user_and_theme_rendering_do_not_read_sources():
    manager = fixture_manager()
    packet = {**capture_panel(manager, 'completion', '2026-10-01', '2026-10-08'), 'request_id': 'one'}
    manager.reset_mock()
    figure, _ = render_panel('completion', request_for(), packet, None, [], 1, ['Riya'])
    assert not figure.data
    manager.get_user_breakdown_df.assert_not_called()


def test_snapshot_writer_owns_a_copy_while_new_captures_continue():
    manager = fixture_manager()
    capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')
    payload = {'cache': {}}
    attach_panels(manager, payload)
    persisted = deepcopy(payload)
    capture_panel(manager, 'pending', '2026-10-01', '2026-10-08')
    assert payload == persisted
    assert len(manager._snapshot_payload['dashboard_panels']) == 2


def test_mapping_change_during_capture_does_not_publish_mixed_names():
    manager = fixture_manager()
    def remap(*args, **kwargs):
        manager.user_mapping['account'] = 'Priya'
        return pd.DataFrame([{'User': 'Riya', 'Completed Duration': 3600}])
    manager.get_user_breakdown_df.side_effect = remap
    assert not capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')['available']
    assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') is None


def test_invalid_numeric_capture_retains_good_data_and_projection_omits_audit_columns():
    manager = fixture_manager()
    manager.get_user_breakdown_df.return_value['Task Evidence'] = ['large raw inventory']
    packet = capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')
    assert packet['data']['columns'] == ['User', 'Completed Duration']
    manager.get_user_breakdown_df.return_value['Completed Duration'] = float('nan')
    assert not capture_panel(manager, 'completion', '2026-10-01', '2026-10-08')['available']
    assert saved_panel(manager, 'completion', '2026-10-01', '2026-10-08') == packet
