"""User controls and work charts must not depend on live completion or task scans."""
from unittest.mock import MagicMock, patch
import pytest
import dash

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.reporting.user_reports import get_user_report_data, get_user_completion_data, prepare_user_report
from slicing_dashboard.reporting.completed_work import completed_work_for_period
from tests.test_daily_report_page import application_namespace, components, payload_text
from tests.test_user_reports import data
from tests.test_completed_work import manager_with, account


def test_initial_roster_and_callback_dependencies():
    namespace, manager = application_namespace()
    selector = next(node for node in components(namespace['app'].layout)
                    if getattr(node, 'id', None) == 'user-report-person')
    assert selector.options == [{'label': 'Priya', 'value': 'Priya'}, {'label': 'Riya', 'value': 'Riya'}]
    assert selector.value == 'Priya'
    manager.get_user_report_data.assert_not_called()
    callbacks = namespace['app'].callback_map
    main = next(c for key, c in callbacks.items() if 'user-report-content.children' in key)
    assert 'user-completion-store' not in {i['id'] for i in main['inputs']}
    assert 'user-report-store' not in {i['id'] for i in callbacks['user-completion-store.data']['inputs']}


def test_completion_updates_only_its_card_and_rejects_wrong_scope():
    namespace, manager = application_namespace()
    completed = {'start': '2026-09-20', 'end': '2026-10-06', 'available': True,
                 'rows': [{'User': 'A', 'seconds': 7200, 'tasks': 10}], 'captured_at': 'saved'}
    card, note = namespace['render_user_completion'](completed, 'A', data(), '/reports/user')
    assert '02:00:00' in payload_text(card) and '10 completed tasks' in payload_text(card)
    assert 'saved' in note
    completed['start'] = '2026-08-01'
    assert 'Completion data unavailable' in payload_text(
        namespace['render_user_completion'](completed, 'A', data(), '/reports/user')[0])
    manager.get_user_report_data.assert_not_called()
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['load_user_completion']('/reports/daily', 0, 0, '2026-10-06')


def test_saved_cache_preserves_overall_and_remaps_people_and_batches(tmp_path):
    manager = DataManager.__new__(DataManager)
    manager.db = MagicMock()
    manager.db.daily_chart_start_date.return_value = '2026-06-01'
    record = {'date': '2026-06-01', 'metadata': {'available': True},
              'tasks': [{'username': 'one', 'bucket': 'Fresh', 'duration_seconds': 900}]}
    manager.db.load_daily_chart_reports.return_value = [record]
    manager.user_mapping = {'one': 'A'}
    manager.user_mapping_full = {}
    manager._snapshot_payload = {}
    manager._batches_master_cache = {'b': {'batch_date': '2026-10-06', 'username': 'one',
                                         'canonical_user': 'Old', 'assignee_id': 1}}
    manager.settlement_periods = []
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path), \
         patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.reporting.user_reports.today_iso', return_value='2026-10-06'):
        payload = manager.get_user_report_data('2026-10-06')
        assert prepare_user_report(payload, 'A')['overall']['seconds'] == 900
        assert payload['batches'][0]['canonical_user'] == 'A'
        payload['days'].clear()
        assert manager.get_user_report_data('2026-10-06')['days']
        manager.db.load_daily_chart_reports.assert_called_once_with('2026-06-01', '2026-10-06')
        manager.user_mapping['one'] = 'B'
        remapped = manager.get_user_report_data('2026-10-06')
        assert prepare_user_report(remapped, 'B')['overall']['seconds'] == 900
        assert remapped['batches'][0]['canonical_user'] == 'B'
        assert 'tasks' not in remapped['days']['2026-06-01']


def test_interactive_completion_does_not_wait_for_primary_snapshot_write():
    manager = manager_with({'items': [account()], 'total': 1})
    result = completed_work_for_period(manager, '2026-10-01', '2026-10-06', persist=False)
    assert result['available'] and result['rows'][0]['seconds'] == 3600
    manager._save_snapshot.assert_not_called()
    assert manager._cache['completed_report_2026-10-01_2026-10-06']['items']
    manager.get_available_periods.return_value = [data()['current_period']]
    with patch('slicing_dashboard.reporting.user_reports.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.reporting.user_reports.completed_work_for_period') as capture:
        get_user_completion_data(manager, '2026-10-06', True)
    capture.assert_called_once_with(manager, '2026-09-20', '2026-10-06', force_refresh=True, persist=False)


@pytest.mark.parametrize('report_date', ['bad', '2099-01-01'])
def test_invalid_dates_do_not_load_history(report_date):
    manager = MagicMock()
    with pytest.raises(ValueError):
        get_user_report_data(manager, report_date)
    manager.db.load_daily_chart_reports.assert_not_called()
