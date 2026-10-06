"""Individual totals, coverage, persistence bounds and route isolation."""
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
import copy
import json

import dash
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.pages.user_report import build_user_report_content
from slicing_dashboard.plots.work_breakdown_donut import build_work_breakdown_donut
from slicing_dashboard.plots.work_trend_chart import build_individual_work_chart
from slicing_dashboard.reporting.dashboard_reports import (
    aggregate_observations, date_range, summarize_daily, unavailable_day,
)
from slicing_dashboard.reporting.user_reports import get_user_report_data, prepare_user_report, resolve_report_user
from tests.test_daily_report_page import application_namespace, components, payload_text
from tests.test_dashboard_reports import source, task


@pytest.fixture(autouse=True)
def india_today():
    with patch('slicing_dashboard.reporting.user_reports.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.pages.user_report.today_iso', return_value='2026-10-06'):
        yield


def day(key, seconds=60, available=True):
    if not available:
        return unavailable_day(key)
    rows = aggregate_observations([
        {'task_id': 'fresh', 'username': 'A-1', 'user': 'A', 'bucket': 'Fresh', 'duration_seconds': seconds},
        {'task_id': 'same', 'username': 'A-2', 'user': 'A', 'bucket': 'Same-day Rework', 'duration_seconds': seconds},
        {'task_id': 'old', 'username': 'A-1', 'user': 'A', 'bucket': 'Old Rework', 'duration_seconds': seconds},
    ])
    return {'date': key, 'rows': rows, 'metadata': {'available': True, 'captured_at': key + 'T20:00:00+05:30'}}


def data(complete=False):
    days = {key: day(key, available=complete or key >= '2026-10-05') for key in date_range('2026-10-06', 31)}
    return {'report_date': '2026-10-06', 'users': ['A', 'B'], 'days': days,
            'today': days['2026-10-06'], 'yesterday': days['2026-10-05'],
            'current_period': {'start_date': '2026-09-20', 'end_date': '2026-10-06', 'is_current': True},
            'history': [days[key] for key in date_range('2026-10-06')]}


def test_individual_daily_parity_and_category_sum():
    payload = data()
    report = prepare_user_report(payload, 'A')
    assert report['summary'] == summarize_daily(payload['today'], ['A'])
    assert report['summary']['total_seconds'] == 180
    assert report['summary']['rework_percentage'] == pytest.approx(200 / 3)
    assert report['trend'][-1]['total_seconds'] == 180
    assert report['change']['difference_seconds'] == 0
    donut = build_work_breakdown_donut(report['summary'])
    assert sum(donut.data[0].values) == 180


def test_zero_person_and_missing_dates_are_distinct():
    report = prepare_user_report(data(), 'B')
    assert report['summary']['total_seconds'] == 0
    assert report['summary']['rework_percentage'] is None
    assert report['trend'][0]['total_seconds'] is None
    assert report['trend'][-1]['total_seconds'] == 0
    assert report['period']['seconds'] == 0
    assert not build_work_breakdown_donut(report['summary']).data
    assert prepare_user_report(data(), 'not a member') is None


def test_alias_selection_resolver_only_allows_current_roster():
    assert resolve_report_user('a', ['A', 'B']) == 'A'
    assert resolve_report_user('A-2', ['A', 'B'], {'A-2': 'A'}) == 'A'
    assert resolve_report_user('Test', ['A', 'B'], {'Test': 'Exempt'}) is None


def test_settlement_uses_real_boundaries_and_overall_includes_older_history():
    payload = data(complete=True)
    payload['days']['2026-08-01'] = day('2026-08-01', 120)
    payload['days']['2026-10-07'] = day('2026-10-07', 999)
    report = prepare_user_report(payload, 'A')
    assert report['period']['calendar_days'] == 17
    assert report['period']['seconds'] == 17 * 180
    assert report['overall']['seconds'] == 31 * 180 + 360
    assert report['overall']['start'] == '2026-08-01'
    assert report['overall']['partial']


def test_partial_scope_is_reported_without_filling_missing_days():
    report = prepare_user_report(data(), 'A')
    assert report['period']['seconds'] == 360
    assert report['period']['recorded_days'] == 2
    assert report['period']['calendar_days'] == 17
    assert report['period']['partial']
    assert report['average_7'] is None
    assert report['average_30'] is None
    assert report['best_day'] is None


def test_means_exclude_today_and_require_consecutive_recorded_days():
    payload = data(complete=True)
    payload['days']['2026-10-06'] = day('2026-10-06', 9000)
    report = prepare_user_report(payload, 'A')
    assert report['average_7'] == report['average_30'] == 180
    assert report['best_day'][1] == 180
    assert report['trend'][-1]['rolling_mean_seconds'] is None
    assert report['trend'][-2]['rolling_mean_seconds'] == 180
    payload['days']['2026-10-01']['metadata']['is_snapshot'] = True
    report = prepare_user_report(payload, 'A')
    assert report['average_7'] is None
    assert report['average_30'] is None
    assert report['period']['stale']


def test_no_current_period_and_empty_history_are_unavailable():
    payload = data()
    payload['current_period'] = None
    payload['days'] = {key: unavailable_day(key) for key in payload['days']}
    report = prepare_user_report(payload, 'A')
    assert report['period']['seconds'] is None
    assert report['overall']['seconds'] is None
    assert report['summary']['total_seconds'] is None
    assert not build_work_breakdown_donut(report['summary']).data


def test_historical_date_never_counts_later_output_or_future_period():
    payload = data(complete=True)
    payload['report_date'] = '2026-09-19'
    report = prepare_user_report(payload, 'A')
    assert report['period']['seconds'] is None
    assert report['overall']['end'] == '2026-09-19'
    assert report['overall']['seconds'] == 14 * 180
    assert report['trend'][-1]['date'] == '2026-09-19'


def test_loading_reads_history_once_and_selection_never_refetches():
    payload = data()
    manager = MagicMock()
    manager.refresh_scope.side_effect = lambda: nullcontext()
    manager.user_mapping = {'A-1': 'A', 'A-2': 'A', 'B-1': 'B'}
    manager.user_mapping_full = {}
    manager._snapshot_payload = {}
    manager.get_daily_report_data.return_value = payload
    manager.get_available_periods.return_value = [payload['current_period']]
    manager._daily_report_start_date.return_value = '2026-08-01'
    manager._load_daily_report_records.return_value = {'2026-08-01': day('2026-08-01')}
    prepared = get_user_report_data(manager, '2026-10-06', True)
    before = copy.deepcopy(prepared)
    for user in ['A', 'B', 'A']:
        prepare_user_report(prepared, user)
    assert prepared == before
    manager.get_daily_report_data.assert_called_once_with('2026-10-06', force_refresh=True)
    manager._load_daily_report_records.assert_called_once_with('2026-08-01', '2026-10-06')
    assert prepare_user_report(prepared, 'A')['overall']['seconds'] == 540
    json.dumps(prepared, allow_nan=False)


def test_history_start_uses_disk_database_and_memory_without_inventory(tmp_path):
    manager = DataManager.__new__(DataManager)
    manager._daily_report_records = {'2026-10-05': {}}
    manager.db = MagicMock()
    manager.db.daily_work_start_date.return_value = '2026-08-10'
    directory = tmp_path / 'reports' / 'daily-work'
    directory.mkdir(parents=True)
    (directory / '2026-09-01.json').write_text('{}')
    (directory / 'not-a-date.json').write_text('{}')
    (directory / '2099-01-01.json').write_text('{}')
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path):
        assert manager._daily_report_start_date('2026-10-06') == '2026-08-10'


def test_real_manager_user_report_captures_shared_daily_source_and_remaps_aliases(tmp_path):
    manager = DataManager.__new__(DataManager)
    manager.scraper = MagicMock()
    manager.db = MagicMock()
    manager.db.load_daily_work_reports.return_value = []
    manager.db.daily_work_start_date.return_value = None
    manager.db.save_daily_work_report.return_value = False
    manager._cache = {}
    manager._snapshot_payload = {}
    manager.user_mapping = {'SSHD-A': 'A', 'SSHD-A2': 'A', 'SSHD-B': 'B'}
    manager.user_mapping_full = {}
    manager.settlement_periods = [{'start_date': '2026-09-20', 'end_date': '2026-10-06', 'settled': False}]
    with patch('slicing_dashboard.config.DATA_DIR', tmp_path), \
         patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-06'), \
         patch('slicing_dashboard.processing.daily_work_source.DailyWorkSource.fetch',
               return_value=source([task('a'), task('b', raw='SSHD-A2', seconds=90)])) as fetch:
        payload = manager.get_user_report_data(force_refresh=True)
        assert [call.args[0] for call in fetch.call_args_list] == ['2026-10-06', '2026-10-05']
        individual = prepare_user_report(payload, 'A')
        assert individual['summary']['total_seconds'] == 150
        assert individual['period']['seconds'] == individual['overall']['seconds'] == 150
        assert prepare_user_report(payload, 'B')['summary']['total_seconds'] == 0
        assert (tmp_path / 'reports/daily-work/2026-10-06.json').exists()
        manager.scraper._client.get.assert_not_called()


def test_database_history_start_uses_indexed_date_bound():
    database = DatabaseManager.__new__(DatabaseManager)
    database.is_connected = lambda: True
    database.engine = None
    collection = MagicMock()
    collection.find_one.return_value = {'date': '2026-08-10'}
    database.mongo_db = {'daily_work_reports': collection}
    assert database.daily_work_start_date('2026-10-06') == '2026-08-10'
    collection.find_one.assert_called_once_with({'_id': {'$lte': '2026-10-06'}}, {'date': 1}, sort=[('_id', 1)])


def test_trend_gaps_optional_categories_and_person_revision():
    report = prepare_user_report(data(), 'A')
    chart = build_individual_work_chart(report['trend'], 'A')
    assert chart.data[0].y[0] is None
    assert chart.data[0].y[-1] == .05
    assert all(not trace.connectgaps for trace in chart.data)
    assert all(trace.visible == 'legendonly' for trace in chart.data[1:])
    assert chart.layout.uirevision != build_individual_work_chart(report['trend'], 'B').layout.uirevision


def test_page_contains_seven_kpis_scopes_and_all_error_states():
    result = build_user_report_content(data(), 'A')
    cards = [node for node in components(result) if getattr(node, 'className', None) == 'kpi-card']
    assert len(cards) == 7
    text = payload_text(result)
    assert 'Partial' in text and 'Overall recorded' in text
    assert 'Saved submissions' not in text
    assert 'Choose an available' in payload_text(build_user_report_content(data(), 'invalid'))
    assert 'Loading' in payload_text(build_user_report_content(None, 'A'))
    assert 'Bad date' in payload_text(build_user_report_content({'validation_error': 'Bad date'}, 'A'))
    payload = data()
    payload['days']['2026-10-06']['metadata'].update(is_snapshot=True, error='Offline')
    assert 'Showing saved submissions' in payload_text(build_user_report_content(payload, 'A'))


def test_report_children_follow_dash_flat_component_contract():
    # A nested list in report_section made Dash remount Location repeatedly,
    # which continuously reloaded reports and hid their content behind Loading.
    for node in components(build_user_report_content(data(), 'A')):
        children = getattr(node, 'children', None)
        if isinstance(children, (list, tuple)):
            assert not any(isinstance(child, (list, tuple)) for child in children)


def test_user_callbacks_are_active_route_only_and_selection_is_pure():
    namespace, manager = application_namespace()
    route = namespace['route_pages']('/reports/user')
    assert route[0] == route[1] == route[2] == {'display': 'none'}
    assert route[7] == {} and route[8]
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['load_user_report']('/reports/daily', 1, 0, '2026-10-06')
    payload = data()
    for user in ('A', 'B'):
        namespace['render_user_report'](payload, user, 1, '/reports/user')
    manager.get_user_report_data.assert_not_called()
    manager.get_daily_report_data.assert_not_called()
    load_callback = next(value for key, value in namespace['app'].callback_map.items() if key == 'user-report-store.data')
    assert 'user-report-person' not in {entry['id'] for entry in load_callback['inputs']}
    manager.get_user_report_data.return_value = payload
    assert namespace['load_user_report']('/reports/user', 1, 0, '2026-10-06') == payload
    manager.get_user_report_data.assert_called_once()
    manager.get_user_report_data.side_effect = ValueError('date')
    assert 'validation_error' in namespace['load_user_report']('/reports/user', 1, 0, 'bad')


def test_roster_preserves_selection_and_recovers_after_mapping_changes():
    namespace, _ = application_namespace()
    assert namespace['update_report_people'](data(), 'B')[1] == 'B'
    assert namespace['update_report_people'](data(), 'Removed')[1] == 'A'
    assert namespace['update_report_people']({'users': []}, 'B') == ([], None)


def test_user_date_follows_midnight_only_when_not_pinned():
    namespace, _ = application_namespace()
    with patch.dict(namespace, {'today_iso': lambda: '2026-10-07'}), \
         patch('dash.callback_context', MagicMock(triggered=[{'prop_id': 'auto-refresh-interval.n_intervals'}])):
        assert namespace['follow_user_date'](0, 1, '2026-10-06', True)[0] == '2026-10-07'
        assert namespace['follow_user_date'](0, 1, '2026-10-05', False)[0] is dash.no_update
