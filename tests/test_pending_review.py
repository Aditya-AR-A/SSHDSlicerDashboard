"""Current review values cannot come from a historical or superseded capture."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.processing.pending_review import PendingReviewCapture, current_review_end, fetch_items, pending_frame
from slicing_dashboard.plots.pending_chart import build_pending_chart


def account(uid=1, name='account', seconds=3600):
    return {'user_id': uid, 'username': name, **{
        key: value for stage in ('leader', 'auditor', 'admin')
        for key, value in ((f'{stage}_review_duration_seconds', seconds if stage == 'leader' else 0),
                           (f'{stage}_review_count', 1 if stage == 'leader' else 0))}}


def manager():
    manager = DataManager.__new__(DataManager)
    manager.scraper = MagicMock(is_authenticated=True)
    manager.scraper._base_url = 'https://source.test'
    manager._cache = {'eff_2_2020-01-01_2026-10-07': ({}, [account(seconds=999999)])}
    manager.user_mapping = {'account': 'Person', 'alias': 'Person'}
    return manager


def response(payload):
    result = MagicMock()
    result.json.return_value = payload
    return result


@pytest.fixture(autouse=True)
def stable_review_date():
    with patch('slicing_dashboard.processing.pending_review.current_review_end', return_value='2026-10-07'):
        yield


def test_latest_api_matches_every_stage_after_alias_mapping_and_remapping():
    dm = manager()
    rows = [account(), account(2, 'alias', 1800)]
    rows[1]['admin_review_duration_seconds'] = 23
    rows[1]['admin_review_count'] = 2
    dm.scraper._client.get.return_value = response({'items': rows, 'total': 2})
    frame = pending_frame(dm)
    assert frame.set_index('Stage').Duration.to_dict() == {'Pending Leader': 5400, 'Pending Auditor': 0, 'Pending Admin': 23}
    figure = build_pending_chart(frame, True)
    assert {trace.name: sum(trace.y) * 3600 for trace in figure.data} == {'Pending Leader': 5400, 'Pending Admin': 23}
    assert frame.attrs['captured_at'] and not frame.attrs['pending_error']
    dm.user_mapping['alias'] = 'Other person'
    assert set(pending_frame(dm).User) == {'Person', 'Other person'}
    assert dm.scraper._client.get.call_count == 1


def test_refresh_failure_never_restores_previous_or_other_range_cache():
    dm = manager()
    dm.scraper._client.get.side_effect = [response({'items': [account()], 'total': 1}),
                                        ConnectionError(), response({'items': [], 'total': 0})]
    assert not pending_frame(dm).empty
    failed = pending_frame(dm, True)
    assert failed.empty and failed.attrs['pending_error']
    assert 'unavailable' in build_pending_chart(failed, True).layout.annotations[0].text
    assert pending_frame(dm).empty  # failed capture is not cached
    assert dm.scraper._client.get.call_count == 3


def test_expiry_date_boundary_and_targeted_invalidation():
    dm = manager()
    dm.scraper._client.get.return_value = response({'items': [], 'total': 0})
    with patch('slicing_dashboard.processing.pending_review.monotonic', return_value=100):
        pending_frame(dm)
        pending_frame(dm)
    with patch('slicing_dashboard.processing.pending_review.monotonic', return_value=161):
        pending_frame(dm)
    dm.invalidate_pending_review()
    pending_frame(dm)
    with patch('slicing_dashboard.processing.pending_review.current_review_end', return_value='2026-10-08'):
        pending_frame(dm)
    assert dm.scraper._client.get.call_count == 4
    assert dm.scraper._client.get.call_args.kwargs['params']['end_date'] == '2026-10-08'
    assert dm.scraper._client.get.call_args.kwargs['params']['start_date'] == '2020-01-01'


def test_source_next_day_is_included_at_india_evening_boundary():
    from datetime import datetime
    with patch('slicing_dashboard.processing.pending_review.today_iso', return_value='2026-10-07'), \
         patch('slicing_dashboard.processing.pending_review.datetime') as clock:
        clock.now.return_value = datetime.fromisoformat('2026-10-08T00:15:00+08:00')
        assert current_review_end() == '2026-10-08'


def test_slow_old_response_cannot_overwrite_or_return_instead_of_new_capture():
    cache = PendingReviewCapture()
    started, release = Event(), Event()
    def source(scraper, end):
        if scraper == 'old':
            started.set()
            assert release.wait(3)
            return [account(seconds=9000)]
        return [account(seconds=60)]
    with patch('slicing_dashboard.processing.pending_review.fetch_items', side_effect=source):
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(cache.read, 'old', True)
            assert started.wait(3)
            newest = cache.read('new', True)
            release.set()
            assert old.result() == newest
        assert cache.read('unused')['items'][0]['leader_review_duration_seconds'] == 60


def test_rollover_does_not_join_a_previous_day_request():
    cache = PendingReviewCapture()
    started, release = Event(), Event()
    end = {'date': '2026-10-07'}
    def source(scraper, day):
        if day == '2026-10-07':
            started.set()
            assert release.wait(3)
            return [account(seconds=9000)]
        return [account(seconds=60)]
    with patch('slicing_dashboard.processing.pending_review.current_review_end', side_effect=lambda: end['date']), \
         patch('slicing_dashboard.processing.pending_review.fetch_items', side_effect=source):
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(cache.read, 'source')
            assert started.wait(3)
            end['date'] = '2026-10-08'
            newest = cache.read('source')
            release.set()
            assert old.result() == newest
            assert newest['end'] == '2026-10-08'


@pytest.mark.parametrize('payload', [
    {}, {'items': [account()], 'total': 2},
    {'items': [account(), account(name='different alias')], 'total': 2},
    {'items': [{**account(), 'leader_review_duration_seconds': None}], 'total': 1},
    {'items': [{**account(), 'leader_review_duration_seconds': float('nan')}], 'total': 1},
    {'items': [{**account(), 'admin_review_count': -1}], 'total': 1},
])
def test_incomplete_duplicate_or_invalid_response_is_not_a_plausible_total(payload):
    dm = manager()
    dm.scraper._client.get.return_value = response(payload)
    with pytest.raises(ValueError):
        fetch_items(dm.scraper, '2026-10-07')


def test_short_page_with_remaining_total_is_fully_paginated():
    dm = manager()
    dm.scraper._client.get.side_effect = [response({'items': [account()], 'total': 2}),
                                        response({'items': [account(2, 'alias')], 'total': 2})]
    assert len(fetch_items(dm.scraper, '2026-10-07')) == 2


def test_zero_duration_with_pending_tasks_is_not_dropped():
    dm = manager()
    dm.scraper._client.get.return_value = response({'items': [account(seconds=0)], 'total': 1})
    figure = build_pending_chart(pending_frame(dm), True)
    assert list(figure.data[0].y) == [0]
    assert figure.data[0].customdata[0][2] == 1
