from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.processing.current_queue import fetch_queue
from slicing_dashboard.processing.current_queue import assignment_metadata
from slicing_dashboard.plots.assigned_chart import build_assigned_chart


def manager():
    dm = DataManager.__new__(DataManager)
    dm._cache = {'live_slice_assigned': {'Aditya': {'backlog_dur': 7461}}}
    dm._batches_master_cache = {'old': {'batch_id': 'old', 'username': 'SSHD-S-Aditya3',
        'status': 'batch_member_assigned', 'total_duration_seconds': 7461, 'task_count': 67}}
    dm.scraper = MagicMock(is_authenticated=True)
    dm.scraper._users = {1: {}}
    dm._get_reporting_name = lambda uid, name: 'Aditya'
    dm.fetch_annotator_efficiency = MagicMock(return_value=({}, []))
    return dm


def task(identifier, duration, account='SSHD-S-Aditya3', status='slice_assigned'):
    return {'id': identifier, 'slicer_id': 1, 'slicer': account, 'status': status, 'duration_seconds': duration}


def test_submitted_account_does_not_reappear_from_ledger_or_snapshot():
    dm = manager()
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', return_value=[]):
        frame = dm.get_detailed_pending_assigned_df(overview_data={}, force_refresh=True)
    assert frame[frame.Stage.isin(['New Assigned', 'Rework Assigned'])].empty
    assert not frame.attrs['assignment_errors']
    assert build_assigned_chart(frame[frame.Stage == 'New Assigned'], True).layout.annotations[0].text == 'No assigned work active'


def test_current_assignments_keep_accounts_and_rework_separate():
    dm = manager()
    inventories = [[task('a', 60), task('b', 120, 'OtherAccount')],
                   [task('c', 90, status='slice_rework')]]
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=inventories):
        frame = dm.get_detailed_pending_assigned_df(overview_data={})
    rows = frame[frame.Stage.isin(['New Assigned', 'Rework Assigned'])]
    assert len(rows) == 3
    assert rows[rows.Stage == 'New Assigned'].Duration.sum() == 180
    assert rows[rows.Stage == 'Rework Assigned'].Duration.sum() == 90
    assert set(rows.ID) == {'SSHD-S-Aditya3', 'OtherAccount'}
    assert set(rows.AssignedDate) == {''}


def test_current_queue_cache_expires_and_force_refresh_removes_submissions():
    dm = manager()
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=[[task('a', 60)], [], []]) as fetch:
        assert dm.fetch_all_assigned_tasks_live()['Aditya']['backlog_dur'] == 60
        dm.fetch_all_assigned_tasks_live()
        assert fetch.call_count == 1
        dm._current_queue_cache['slice_assigned'] = (0, dm._current_queue_cache['slice_assigned'][1])
        assert dm.fetch_all_assigned_tasks_live() == {}
        assert dm.fetch_all_assigned_tasks_live(force_refresh=True) == {}
        assert fetch.call_count == 3


def test_cached_raw_queue_uses_current_mappings_and_exclusions():
    dm = manager()
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', return_value=[task('a', 60)]) as fetch:
        assert dm.fetch_all_assigned_tasks_live()['Aditya']['backlog_dur'] == 60
        dm._get_reporting_name = lambda uid, name: 'Riya'
        assert dm.fetch_all_assigned_tasks_live()['Riya']['backlog_dur'] == 60
        dm._get_reporting_name = lambda uid, name: 'Unassigned'
        assert dm.fetch_all_assigned_tasks_live() == {}
        assert fetch.call_count == 1


def test_cursor_pagination_deduplicates_and_ignores_submitted_tasks():
    with patch('slicing_dashboard.processing.current_queue.DailyWorkSource') as source:
        source.return_value.request.side_effect = [
            {'data': [task('a', 60)], 'meta': {'has_more': True, 'next_cursor': 'next'}},
            {'data': [task('a', 60), task('b', 90), task('submitted', 7200, status='slice_submitted')],
             'meta': {'has_more': False}},
        ]
        assert [r['id'] for r in fetch_queue(MagicMock(), 'slice_assigned')] == ['a', 'b']
        assert source.return_value.request.call_args_list[1].kwargs['params']['cursor'] == 'next'


def test_failed_refresh_cannot_restore_previous_assignment_capture():
    dm = manager()
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=[[task('a', 60)], ConnectionError(), []]) as fetch:
        assert dm.fetch_all_assigned_tasks_live()['Aditya']['backlog_dur'] == 60
        with pytest.raises(ConnectionError):
            dm.fetch_all_assigned_tasks_live(force_refresh=True)
        assert dm.fetch_all_assigned_tasks_live() == {}
        assert fetch.call_count == 3


@pytest.mark.parametrize('meta', [{'has_more': False, 'total_capped': True},
                                  {'has_more': True, 'next_cursor': 'same'}])
def test_incomplete_inventory_is_an_error(meta):
    with patch('slicing_dashboard.processing.current_queue.DailyWorkSource') as source:
        source.return_value.request.return_value = {'data': [task('a', 60)], 'meta': meta}
        with pytest.raises(ValueError):
            fetch_queue(MagicMock(), 'slice_assigned')


def test_partial_failure_preserves_successful_queue_with_visible_warning():
    dm = manager()
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=[ConnectionError(), [task('c', 90, status='slice_rework')]]):
        frame = dm.get_detailed_pending_assigned_df(overview_data={})
    figure = build_assigned_chart(frame[frame.Stage == 'Rework Assigned'], True)
    assert 'incomplete' in figure.layout.annotations[0].text
    assert figure.data[-1].customdata[0][4] == 'Unknown'


def test_assignment_date_and_age_return_without_using_historical_hours():
    dm = manager()
    dm._batches_master_cache['old']['batch_date'] = '2026-10-05'
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=[[task('a', 60)], []]), \
            patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-07'):
        frame = dm.get_detailed_pending_assigned_df(overview_data={})
    assigned = frame[frame.Stage == 'New Assigned']
    assert assigned.iloc[0].Duration == 60
    assert assigned.iloc[0].AssignedDate == '2026-10-05'
    assert assigned.iloc[0].DaysAssigned == 2
    assert assigned.iloc[0].AssignmentDateBasis == 'Account batch date'
    bar = build_assigned_chart(assigned, True).data[-1]
    assert bar.marker.color[0] == '#22c55e'
    assert '2026-10-05' in bar.customdata[0][4] and '2d ago' in bar.customdata[0][4]


def test_exact_batch_dates_keep_two_assignments_for_one_account_separate():
    dm = manager()
    dm._batches_master_cache = {batch: {'batch_id': batch, 'batch_date': day,
        'username': 'SSHD-S-Aditya3', 'status': 'batch_member_assigned', 'total_duration_seconds': 7200}
        for batch, day in [('one', '2026-10-04'), ('two', '2026-10-06')]}
    tasks = [{**task('a', 60), 'assignment_batch_id': 'one'},
             {**task('b', 120), 'assignment_batch_id': 'two'}]
    with patch('slicing_dashboard.processing.current_queue.fetch_queue', side_effect=[tasks, []]), \
            patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-07'):
        frame = dm.get_detailed_pending_assigned_df(overview_data={})
    rows = frame[frame.Stage == 'New Assigned']
    assert set(rows.AssignedDate) == {'2026-10-04', '2026-10-06'}
    assert set(rows.DaysAssigned) == {1, 3}
    assert rows.Duration.sum() == 180


def test_legacy_return_links_exact_rework_batch_and_ambiguous_dates_stay_unknown():
    batches = [{'batch_id': 'one', 'assignee_id': 1, 'status': 'batch_rework', 'batch_date': '2026-10-03'},
               {'batch_id': 'two', 'assignee_id': 1, 'status': 'batch_member_assigned', 'batch_date': '2026-10-06'}]
    row = {**task('a', 90, status='slice_rework'), 'slice_batch': 4}
    assert assignment_metadata(row, batches)['AssignedDate'] == ''
    linked = assignment_metadata(row, batches, [{'user_id': 1, 'legacy_batch_number': 4, 'batch_id': 'one'}])
    assert linked['AssignedDate'] == '2026-10-03' and linked['BatchID'] == 'one'
    assert linked['AssignmentDateBasis'] == 'Batch assignment date'
    direct = assignment_metadata({**row, 'assigned_at': '2026-10-07T01:00:00+08:00'}, batches)
    assert direct['AssignedDate'] == '2026-10-06'
