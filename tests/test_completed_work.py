"""Settlement completion must not reuse submissions, partial pages or foreign ranges."""
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from slicing_dashboard.reporting.completed_work import completed_work_for_period
from slicing_dashboard.reporting.user_reports import prepare_user_report
from tests.test_user_reports import data
from tests.test_daily_report_page import application_namespace


def account(name='A-1', uid=1, seconds=3600, tasks=5):
    return {'username': name, 'user_id': uid, 'completed_duration_seconds': seconds, 'completed_count': tasks}


def manager_with(*pages):
    manager = MagicMock()
    manager._cache = {}
    manager._get_reporting_name.side_effect = lambda uid, name: {'A-1': 'A', 'A-2': 'A', 'Skip': 'Exempt'}.get(name, name)
    responses = []
    for payload in pages:
        response = MagicMock()
        response.json.return_value = payload
        responses.append(response)
    manager.scraper._client.get.side_effect = responses
    return manager


def fetch(manager, start='2026-10-01', end='2026-10-06', force=False):
    return completed_work_for_period(manager, start, end, force_refresh=force)


def test_aliases_are_combined_exclusions_applied_and_cached_selection_is_pure():
    manager = manager_with({'items': [account(), account('A-2', 2, 1800, 3), account('Skip', 3)], 'total': 3})
    result = fetch(manager)
    assert result['available'] and result['rows'] == [{'User': 'A', 'seconds': 5400, 'tasks': 8}]
    assert not result['is_snapshot']
    before = deepcopy(manager._cache)
    result['rows'][0]['seconds'] = 99
    assert fetch(manager)['rows'][0]['seconds'] == 5400
    assert before == manager._cache
    assert manager.scraper._client.get.call_count == 1
    manager._save_snapshot.assert_called_once()
    manager._get_reporting_name.side_effect = lambda uid, name: 'Renamed' if name != 'Skip' else 'Exempt'
    assert fetch(manager)['rows'][0]['User'] == 'Renamed'
    assert manager.scraper._client.get.call_count == 1


def test_forced_refresh_and_expiry_fetch_again():
    manager = manager_with(*[{'items': [account(seconds=s)], 'total': 1} for s in [3600, 7200, 9000]])
    assert fetch(manager)['rows'][0]['seconds'] == 3600
    assert fetch(manager, force=True)['rows'][0]['seconds'] == 7200
    manager._cache['completed_report_2026-10-01_2026-10-06']['captured_at'] = '2020-01-01T00:00:00+00:00'
    assert fetch(manager)['rows'][0]['seconds'] == 9000
    assert manager.scraper._client.get.call_count == 3


def test_failed_refresh_uses_only_exact_range_and_marks_capture_saved():
    manager = manager_with({'items': [account()], 'total': 1})
    saved = fetch(manager)
    manager.scraper._client.get.side_effect = ConnectionError('secret URL must not reach UI')
    result = fetch(manager, force=True)
    assert result['rows'] == saved['rows'] and result['is_snapshot']
    assert result['captured_at'] == saved['captured_at']
    assert result['error'] == 'ConnectionError'
    other = fetch(manager, start='2026-09-01')
    assert not other['available'] and not other['rows']


@pytest.mark.parametrize('payload', [
    {}, {'items': None}, {'items': [account()], 'total': 2},
    {'items': [account(seconds=None)], 'total': 1},
    {'items': [account(seconds=-1)], 'total': 1},
    {'items': [account(seconds=float('nan'))], 'total': 1},
    {'items': [account(tasks=1.5)], 'total': 1},
    {'items': [account(), account()], 'total': 2},
])
def test_incomplete_and_invalid_responses_are_unknown_not_zero(payload):
    manager = manager_with(payload)
    result = fetch(manager)
    assert not result['available'] and result['rows'] == []
    assert not manager._cache


def test_complete_pagination_and_recorded_zero_are_valid():
    first = [account(str(uid), uid, 0, 0) for uid in range(200)]
    manager = manager_with({'items': first, 'total': 201}, {'items': [account()], 'total': 201})
    result = fetch(manager)
    assert result['available'] and len(result['rows']) == 201
    assert manager.scraper._client.get.call_args_list[1].kwargs['params']['page'] == 2
    empty = fetch(manager_with({'items': [], 'total': 0}))
    assert empty['available'] and empty['rows'] == []


def test_future_or_missing_period_never_reads_source():
    manager = manager_with()
    assert not fetch(manager, start='2026-10-07')['available']
    assert not fetch(manager, start=None)['available']
    manager.scraper._client.get.assert_not_called()


def test_completion_is_separate_from_work_and_ignores_wrong_period():
    payload = data()
    payload['completed_period'] = {'start': '2026-09-20', 'end': '2026-10-06', 'available': True,
                                   'rows': [{'User': 'A', 'seconds': 7200, 'tasks': 10}]}
    report = prepare_user_report(payload, 'A')
    assert report['completed_period']['seconds'] == 7200
    assert report['completed_period']['tasks'] == 10
    assert report['period']['seconds'] == 240
    assert prepare_user_report(payload, 'B')['completed_period']['seconds'] == 0
    payload['completed_period']['start'] = '2026-08-01'
    assert prepare_user_report(payload, 'A')['completed_period']['seconds'] is None


@pytest.mark.parametrize('platform,reload_code', [('win32', False), ('linux', True)])
def test_local_runner_avoids_windows_reloader_socket_path(platform, reload_code):
    namespace, _ = application_namespace()
    with patch.object(namespace['sys'], 'platform', platform), patch.object(namespace['app'], 'run') as run:
        namespace['run_local'](port=8057)
    run.assert_called_once_with(debug=True, port=8057, use_reloader=reload_code, dev_tools_hot_reload=reload_code)
