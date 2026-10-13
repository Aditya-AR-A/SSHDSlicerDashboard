from unittest.mock import MagicMock, patch

import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.processing.assignable_pool import parse_inventory
from slicing_dashboard.plots.kpi_cards import build_kpi_layout
from tests.test_daily_report_page import payload_text


def inventory(normal=0, urgent=0, normal_count=0, urgent_count=0):
    return {'workflow_type': 'slice', 'pool_scope': 'unassigned', 'pending_status': 'slice_pending_assign',
            'normal': {'task_count': normal_count, 'duration_hours': normal},
            'urgent': {'task_count': urgent_count, 'duration_hours': urgent}}


def response(payload, status=200):
    result = MagicMock(status_code=status)
    result.json.return_value = payload
    return result


def manager():
    dm = DataManager.__new__(DataManager)
    dm.scraper = MagicMock(is_authenticated=True)
    dm.scraper._base_url = 'http://source.test'
    dm.scraper._client.get.return_value = response(inventory())
    dm.db = MagicMock()
    dm._batches_master_cache = {}
    dm.user_mapping = {}
    dm.fetch_dashboard_data = MagicMock(return_value={
        'metrics': {'overview_slice_assignable_remaining_duration_seconds': 99999},
        'breakdowns': {'slice_funnel': [{'key': 'pending_assign', 'duration_seconds': 99999, 'count': 999}]}})
    dm.fetch_annotator_efficiency = MagicMock(return_value=({}, []))
    dm._current_task_stats = MagicMock(return_value={})
    return dm


def test_supplied_zero_response_and_both_pool_units():
    zero = parse_inventory(inventory())
    assert zero['available'] and zero['duration_seconds'] == 0 and zero['task_count'] == 0
    totals = parse_inventory(inventory(1.25, .5, 8, 3))
    assert totals['duration_seconds'] == 6300
    assert totals['task_count'] == 11


@pytest.mark.parametrize('field,value', [('pool_scope', 'assigned'), ('workflow_type', 'desensitize'),
                                       ('pending_status', 'slice_assigned'), ('urgent', None)])
def test_incomplete_or_wrong_scope_is_not_a_verified_zero(field, value):
    payload = inventory()
    payload[field] = value
    with pytest.raises(ValueError):
        parse_inventory(payload)


@pytest.mark.parametrize('field,value', [('duration_hours', None), ('duration_hours', -1),
                                       ('duration_hours', float('nan')), ('duration_hours', float('inf')),
                                       ('duration_hours', 1e308), ('task_count', -1),
                                       ('task_count', 1.5), ('task_count', True)])
def test_invalid_numbers_are_rejected(field, value):
    payload = inventory()
    payload['normal'][field] = value
    with pytest.raises(ValueError):
        parse_inventory(payload)


def test_cache_expiry_and_failed_refresh_cannot_restore_old_pool_hours():
    dm = manager()
    dm.scraper._client.get.side_effect = [response(inventory(2)), response(inventory()),
                                         ConnectionError('Private upstream details'), response(inventory())]
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=100):
        assert dm.get_assignable_pool()['duration_seconds'] == 7200
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=159):
        assert dm.get_assignable_pool()['duration_seconds'] == 7200
        assert dm.scraper._client.get.call_count == 1
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=161):
        assert dm.get_assignable_pool()['duration_seconds'] == 0
        failed = dm.get_assignable_pool(force_refresh=True)
        assert not failed['available'] and failed['duration_seconds'] is None
        assert failed['error'] == 'ConnectionError'
        assert dm.get_assignable_pool()['duration_seconds'] == 0


@pytest.mark.parametrize('users', [None, ['Aditya']])
def test_kpis_and_detail_share_one_current_pool_despite_stale_overview_and_filters(tmp_path, users):
    dm = manager()
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path), dm.refresh_scope():
        kpis = dm.get_summary_kpis('2026-09-01', '2026-09-30', selected_users=users, force_refresh=True)
        detailed = dm.get_detailed_pending_assigned_df('2026-09-01', '2026-09-30',
                                                      force_refresh=True, include_pending=False)
    assert kpis['assignable_duration'] == 0 and kpis['assignable_count'] == 0
    pool = detailed[detailed['User'] == 'Assignable Pool'].iloc[0]
    assert pool['Duration'] == 0 and pool['Count'] == 0
    assert dm.scraper._client.get.call_count == 1
    args, kwargs = dm.scraper._client.get.call_args
    assert args[0].endswith('/api/slice/pool-inventory') and 'params' not in kwargs
    cards = build_kpi_layout(kpis, {'pending_assign': {'duration_seconds': 99999}}, True)
    card = payload_text(cards[2])
    assert '00:00' in card and 'Currently assignable' in card


def test_pool_failure_only_marks_pool_unavailable_and_does_not_fabricate_zero():
    dm = manager()
    dm.scraper._client.get.side_effect = ConnectionError('Private upstream URL')
    kpis = dm.get_summary_kpis('2026-10-01', '2026-10-08')
    assert kpis['assignable_duration'] is None and kpis['total_approved_duration'] == 0
    cards = build_kpi_layout(kpis, {'pending_assign': {'duration_seconds': 99999}}, True)
    assert 'Unavailable' in payload_text(cards[2])
    assert 'Pool unavailable' in payload_text(cards[2])
    frame = dm.get_detailed_pending_assigned_df(include_pending=False)
    assert frame[frame['User'] == 'Assignable Pool'].empty
    assert frame.attrs['assignable_error'] == 'ConnectionError'


def test_expired_cookie_reauthenticates_once_without_storing_a_session_cookie():
    dm = manager()
    dm.scraper._client.get.side_effect = [response({}, 401), response(inventory(1, 0, 2))]
    dm.scraper.login.return_value = True
    assert dm.get_assignable_pool()['duration_seconds'] == 3600
    dm.scraper.login.assert_called_once()
    assert dm.scraper._client.get.call_count == 2
    assert all('Cookie' not in call.kwargs['headers'] for call in dm.scraper._client.get.call_args_list)
