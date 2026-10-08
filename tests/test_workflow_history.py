"""Durable workflow projections, policy inference and shared notifications."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dash
import pytest

from slicing_dashboard.processing.workflow_events import project_events, project_notices
from slicing_dashboard.processing.workflow_history import WorkflowStore, WorkflowHistory, observation
from slicing_dashboard.reporting.workflow_history import history_page, notice_link
from tests.test_daily_report_page import application_namespace, components, payload_text

NOW = '2026-10-07T10:00:00+00:00'
INSTANCE = 'fixture'


def evidence(source, row, phase=None, instance=INSTANCE):
    result = observation(source, row, instance, {}, lambda uid, name: name, phase)
    result['observed_at'] = NOW
    return result


def review(identifier, day=1, decision='approved', status='batch_reviewing', **extras):
    return evidence('batch-review', {
        'review_log_id': identifier, 'assignment_batch_id': 'ab_example',
        'reviewed_at': f'2026-10-{day:02d}T12:00:00+08:00', 'review_decision': decision,
        'review_action': 'BATCH_REVIEW', 'review_current_status': status, 'reviewer_id': 5,
        'assignee_id': 10, 'assignee_username': 'Member', 'workflow_type': 'slice', **extras,
    })


def request(identifier, index=1, kind='sampling_pass_notice', **extras):
    return {'id': str(identifier), 'request_type': kind, 'workflow_type': 'slice',
            'task_id': f'task_{identifier}', 'task_desensitize_id': f'public_{identifier}',
            'requester_name': 'Member', 'requester_id': 10, 'status': 'request_pending',
            'reason': 'Correct edges' if kind == 'rework_notice' else 'Quality requirements met',
            'created_at': f'2026-10-{index:02d}T12:00:00+08:00',
            'decision_note': 'assignment_batch_id=ab_example', **extras}


def completed():
    return {'batch_id': 'ab_example', 'status': 'batch_completed', 'canonical_user': 'Member',
            'last_updated': NOW, 'total_duration_seconds': 3600}


def active(rows):
    return [row for row in rows if row.get('active')]


def test_completed_batch_fills_all_missing_stages_without_inventing_times_or_actors():
    events = project_events([], [completed()], [], INSTANCE, NOW)
    assert {row['stage'] for row in events} == {'Leader', 'Auditor', 'Admin'}
    assert all(row['is_synthetic'] and row['event_at'] is None and row['actor_id'] is None for row in events)
    assert all(row['video_seconds'] is None for row in events)
    assert all(row['batch_video_seconds_at_capture'] == 3600 for row in events)
    repeated = project_events([], [completed()], events, INSTANCE, '2026-10-08T10:00:00+00:00')
    assert repeated == events


def test_missing_middle_stage_is_synthetic_and_later_evidence_supersedes_it():
    observed = [review('leader', status='batch_pending_auditor_review'), review('admin', day=3, status='batch_completed')]
    events = project_events(observed, [completed()], [], INSTANCE, NOW)
    assert len(active(events)) == 3
    synthetic = next(row for row in events if row['is_synthetic'])
    assert synthetic['stage'] == 'Auditor'
    observed.append(review('auditor', day=2, status='batch_pending_admin_review'))
    corrected = project_events(observed, [completed()], events, INSTANCE, NOW)
    assert len(active(corrected)) == 3
    obsolete = next(row for row in corrected if row['id'] == synthetic['id'])
    assert not obsolete['active'] and obsolete['superseded_by']
    assert all(not row['is_synthetic'] for row in active(corrected))


def test_generic_approval_order_is_labeled_inferred():
    events = active(project_events([review('one'), review('two', day=2), review('three', day=3)], [], [], INSTANCE, NOW))
    assert [row['stage'] for row in events] == ['Leader', 'Auditor', 'Admin']
    assert all(row['provenance'] == 'Stage inferred' for row in events)


def test_return_resubmit_cycles_are_not_collapsed():
    rows = [review('l1', status='batch_pending_auditor_review'),
            review('a1', day=2, status='batch_pending_admin_review'),
            review('r1', day=3, decision='returned', status='batch_rework'),
            review('l2', day=4, status='batch_pending_auditor_review'),
            review('a2', day=5, status='batch_pending_admin_review'),
            review('admin2', day=6, status='batch_completed')]
    events = active(project_events(rows, [completed()], [], INSTANCE, NOW))
    assert len(events) == 6
    assert len({row['cycle'] for row in events}) == 2
    assert [row['stage'] for row in events if row['cycle'] != 'initial'] == ['Leader', 'Auditor', 'Admin']


def test_stale_completed_ledger_cannot_complete_a_later_return_cycle():
    batch = {**completed(), 'last_updated': '2026-10-01T00:00:00+00:00'}
    events = active(project_events([review('return', day=5, decision='returned', status='batch_rework')],
                                  [batch], [], INSTANCE, NOW))
    assert len(events) == 1 and not events[0]['is_synthetic']


def test_completed_status_on_review_source_fills_missing_prerequisites():
    events = active(project_events([review('leader', status='batch_pending_auditor_review', batch_status='batch_completed')],
                                  [], [], INSTANCE, NOW))
    assert {row['stage'] for row in events} == {'Leader', 'Auditor', 'Admin'}
    assert sum(row['is_synthetic'] for row in events) == 2


def test_notice_pending_is_separate_from_success_or_return_and_no_fake_admin():
    passed = evidence('request', request('pass'), 'created')
    returned = evidence('request', request('return', kind='rework_notice', returned_by=20, returned_by_name='Reviewer'), 'created')
    notices = project_notices([passed, returned], INSTANCE)
    assert [row['category'] for row in notices] == ['success', 'warning']
    assert notices[0]['status'] == 'request_pending' and notices[0]['stage'] is None
    assert notices[1]['actor'] == 'Reviewer'
    events = active(project_events([passed, returned], [], [], INSTANCE, NOW))
    assert all(row['stage'] is None for row in events)


def test_timestamp_boundary_and_batch_note_forms_and_ambiguity():
    row = evidence('request', request('midnight', created_at='2026-10-07T02:00:00',
                   decision_note='assigned_batch_id=ab_example'), 'created')
    assert row['date'] == '2026-10-06'
    assert row['batch_id'] == 'ab_example'
    ambiguous = evidence('request', request('ambiguous', decision_note='assignment_batch_id=ab_one assigned_batch_id=ab_two'), 'created')
    assert ambiguous['batch_id'] is None
    notice = project_notices([ambiguous], INSTANCE)[0]
    assert 'task=task_ambiguous' in notice_link(notice)


def service(tmp_path, requests=None):
    manager = SimpleNamespace(db=None, scraper=SimpleNamespace(_base_url='http://fixture'),
                              _get_reporting_name=lambda uid, name: name, _batches_master_cache={})
    store = WorkflowStore(path=tmp_path / 'workflow.sqlite3')
    history = WorkflowHistory(manager, store)
    source = requests if requests is not None else [request(i, index=i) for i in range(1, 6)]
    def read(path, params=None):
        if path == '/api/auth/me': return {'id': 123}
        if path == '/api/users': return {'items': [{'id': 20, 'username': 'Reviewer', 'role': 8}]}
        if path == '/api/requests':
            assert params['operator_id'] == 123
            page = params.get('page', 1)
            return {'data': deepcopy(source[(page - 1) * 2:page * 2]),
                    'meta': {'page': page, 'page_size': 2, 'total': len(source)}}
        return {'data': [], 'meta': {'has_more': False}}
    history.reader.request = MagicMock(side_effect=read)
    return history, source


def test_pagination_uses_total_and_server_page_size_and_polling_is_idempotent(tmp_path):
    history, _ = service(tmp_path)
    history.sync(force=True)
    assert history.notifications()['total'] == 5
    assert len(history.snapshot()['rows']) == 5
    history.sync(force=True)
    assert history.notifications()['total'] == 5
    assert len(history.snapshot()['rows']) == 5
    assert all(row['complete'] for row in history.snapshot()['checkpoints'])


def test_latest_ten_full_unread_count_restart_read_state_and_late_decision(tmp_path):
    history, rows = service(tmp_path, [request(i, index=(i % 7) + 1) for i in range(1, 13)])
    for _ in range(2): history.sync(force=True)
    inbox = history.notifications()
    assert inbox['total'] == inbox['unread'] == 12 and len(inbox['rows']) == 10
    identifier = inbox['rows'][0]['id']
    history.mark_read(identifier)
    assert history.notifications()['unread'] == 11
    stored = history.store.get(identifier)
    source = next(row for row in rows if row['id'] == stored['source_id'])
    source.update(status='request_approved', decided_by=20, decided_at='2026-10-07T12:00:00+08:00')
    for _ in range(3): history.sync(force=True)
    assert history.notifications()['total'] == 12 and history.notifications()['unread'] == 11
    assert history.store.get(identifier)['status'] == 'request_approved'
    assert any(row['action'] == 'Request Decision' for row in history.store.records('event'))
    restarted = WorkflowHistory(history.manager, WorkflowStore(path=tmp_path / 'workflow.sqlite3'))
    assert restarted.notifications()['unread'] == 11
    assert any(row['is_read'] for row in restarted.notifications()['rows'])


def test_failure_does_not_advance_failed_page_and_recovery_projects_once(tmp_path):
    history, _ = service(tmp_path)
    original = history.reader.request.side_effect
    def fail(path, params=None):
        if path == '/api/requests' and params.get('page') == 2: raise ConnectionError('Sensitive URL must not persist')
        return original(path, params)
    history.reader.request.side_effect = fail
    history.sync(force=True)
    checkpoint = next(row for row in history.snapshot()['checkpoints'] if row['source'] == 'request')
    assert checkpoint['page'] == 2 and not checkpoint['complete'] and checkpoint['error'] == 'ConnectionError'
    assert 'Sensitive' not in str(checkpoint)
    history.reader.request.side_effect = original
    history.sync(force=True)
    assert history.notifications()['total'] == 5


def test_projection_crash_and_retry_reconciles_missing_notifications(tmp_path):
    history, _ = service(tmp_path)
    original = history.store.save_many
    failed = False
    def save(kind, payloads, **kwargs):
        nonlocal failed
        if kind == 'notification' and not failed:
            failed = True
            raise RuntimeError('Simulated crash after event insertion')
        return original(kind, payloads, **kwargs)
    history.store.save_many = save
    with pytest.raises(RuntimeError): history.sync(force=True)
    assert history.store.records('event')
    history.sync(force=True)
    assert history.notifications()['total'] == 5


def test_storage_filters_pagination_inactive_rows_and_literal_search(tmp_path):
    history, _ = service(tmp_path)
    history.sync(force=True)
    page = history_page(history, {'batch_id': 'ab_example'}, page_size=2)
    assert page['total'] == 5 and len(page['rows']) == 2
    assert len(history_page(history, {'search': 'task_1'})['rows']) == 1
    assert history_page(history, {'search': '%'})['total'] == 0
    row = page['rows'][0]
    history.store.save(row['id'], 'event', {**row, 'active': False}, replace=True)
    assert history_page(history)['total'] == 4


def test_persistent_lease_excludes_concurrent_collectors_and_wrong_owner(tmp_path):
    store = WorkflowStore(path=tmp_path / 'workflow.sqlite3')
    other = WorkflowStore(path=tmp_path / 'workflow.sqlite3')
    assert store.acquire_lease(INSTANCE, 'one')
    assert not other.acquire_lease(INSTANCE, 'two')
    other.release_lease(INSTANCE, 'two')
    assert not other.acquire_lease(INSTANCE, 'two')
    store.release_lease(INSTANCE, 'one')
    assert other.acquire_lease(INSTANCE, 'two')


def test_bulk_page_writes_are_atomic_and_scope_isolated(tmp_path):
    store = WorkflowStore(path=tmp_path / 'workflow.sqlite3')
    rows = [{'id': 'one', 'instance': 'a', 'active': True, 'sort_at': NOW},
            {'id': 'two', 'instance': 'b', 'active': True, 'sort_at': NOW}]
    store.save_many('event', rows)
    assert store.query('event', 'a')[1] == 1
    assert store.query('event', 'b')[1] == 1
    store.save_many('event', rows)
    assert len(store.records('event')) == 2


def test_task_alias_filter_and_capped_history_visibility(tmp_path):
    history, _ = service(tmp_path)
    history.sync(force=True)
    assert history_page(history, {'task_id': 'public_1'})['total'] == 1
    def capped(path, params=None):
        if path == '/api/auth/me': return {'id': 123}
        if path == '/api/users': return {'items': []}
        if path == '/api/review/reviewed-batches':
            return {'data': [], 'meta': {'has_more': False, 'total_capped': True}}
        return {'data': [], 'meta': {'total': 0}}
    history.reader.request.side_effect = capped
    history.sync(force=True)
    assert any(row.get('limited') for row in history.snapshot()['checkpoints'])
    assert history.notifications()['total'] == 5  # source disappearance cannot delete persisted notices


def test_observations_exclude_credentials_and_preserve_actor_vs_worker():
    row = evidence('request', request('x', kind='rework_notice', returned_by=20, returned_by_name='Reviewer',
                   pipeline_auth='secret', email='private', password='secret'), 'created')
    assert 'secret' not in str(row) and 'private' not in str(row)
    assert row['member'] == 'Member' and row['actor'] == 'Reviewer'


def test_workflow_route_callbacks_filters_and_read_failure_are_safe():
    namespace, manager = application_namespace()
    route = namespace['route_pages']('/workflow')
    assert route[9] == {} and route[10] and route[2] == {'display': 'none'}
    with pytest.raises(dash.exceptions.PreventUpdate):
        namespace['render_workflow_history']('/', None, *([None] * 11))
    state = {'inbox': {'rows': [], 'unread': 5, 'total': 5}, 'members': ['Member']}
    assert 'Shared team' in payload_text(namespace['render_workflow_notifications'](state, None)[0])
    assert namespace['workflow_deep_link']('?batch=ab_example&event=one')[0] == 'ab_example'
    manager.get_workflow_page.return_value = {'rows': [], 'total': 0, 'storage': 'Test SQLite'}
    result = namespace['render_workflow_history']('/workflow', state, 'Member', 'ab_example', None,
                'Admin', 'Synthetic', 'Approved', None, None, None, 0, 0)
    assert result[2] == 0
    assert manager.get_workflow_page.call_args.args[0]['provenance'] == 'Synthetic'
    manager.get_workflow_data.assert_not_called()
    with patch('dash.ctx', SimpleNamespace(triggered_id={'type': 'workflow-notice', 'id': 'one'})):
        manager.mark_workflow_notification_read.return_value = ('/workflow?batch=ab_example', {})
        assert namespace['read_workflow_notification']([1])[0].startswith('/workflow?batch=')
        manager.mark_workflow_notification_read.side_effect = ConnectionError('secret')
        failed = namespace['read_workflow_notification']([1])
        assert failed[0] is dash.no_update and 'secret' not in str(failed)


def test_mark_read_rejects_other_source_and_unknown_id(tmp_path):
    history, _ = service(tmp_path)
    history.sync(force=True)
    with pytest.raises(ValueError): history.mark_read('unknown')
    row = history.notifications()['rows'][0]
    history.store.save('other', 'notification', {**row, 'instance': 'other'}, replace=True)
    with pytest.raises(ValueError): history.mark_read('other')


def test_clip_logs_do_not_multiply_batch_actions_or_duration():
    rows = [review('batch', status='batch_pending_auditor_review')]
    for i in range(3):
        rows.append(evidence('task-review', {'id': f'clip{i}', 'task_id': f'clip{i}',
            'assignment_batch_id': 'ab_example', 'review_log_id': f'task-log{i}',
            'review_action': 'CONFIRM_SLICE_LEADER', 'review_decision': 'approved',
            'reviewed_at': '2026-10-01T12:00:00+08:00'}))
    events = project_events(rows, [], [], INSTANCE, NOW)
    assert sum(row['action'] == 'Approved' and row['stage'] == 'Leader' for row in events) == 1
    assert all(row['video_seconds'] is None for row in events)


def test_repeated_cursor_is_reported_without_claiming_complete(tmp_path):
    history, _ = service(tmp_path)
    original = history.reader.request.side_effect
    def looping(path, params=None):
        if path == '/api/review/reviewed-batches':
            identifier = params.get('cursor', 'first')
            return {'data': [review(identifier)['raw']], 'meta': {'has_more': True, 'next_cursor': 'same'}}
        return original(path, params)
    history.reader.request.side_effect = looping
    history.sync(force=True)
    state = next(row for row in history.snapshot()['checkpoints'] if row['source'] == 'batch-review')
    assert state['error'] == 'ValueError' and not state['complete']


def test_cli_backfill_stops_after_complete_sources_without_unbounded_scans():
    from click.testing import CliRunner
    from slicing_dashboard.cli import cli
    data = {'events': 10, 'synthetic': 3, 'inbox': {'total': 2, 'unread': 2}, 'storage': 'Test SQLite',
            'checkpoints': [{'last_complete_at': NOW, 'error': None} for _ in range(3)]}
    manager = MagicMock()
    manager.get_workflow_data.return_value = data
    with patch('slicing_dashboard.data_manager.DataManager', return_value=manager):
        result = CliRunner().invoke(cli, ['workflow-sync', '--backfill', '--max-rounds', '3'])
    assert result.exit_code == 0 and 'completed a scan' in result.output
    manager.get_workflow_data.assert_called_once_with(force_refresh=True)


def test_synthetic_correction_keeps_both_projection_versions(tmp_path):
    history, _ = service(tmp_path)
    history.manager._batches_master_cache = {'ab_example': completed()}
    history.sync(force=True)
    synthetic = next(row for row in history.store.records('event')
                     if row['is_synthetic'] and row['stage'] == 'Leader')
    row = evidence('batch-review', review('real-leader', status='batch_pending_auditor_review')['raw'],
                   instance=history.instance)
    history.store.save(row['id'], 'observation', row)
    history.capture_batches()
    assert not history.store.get(synthetic['id'])['active']
    versions = [row['projection'] for row in history.store.records('projection-history')
                if row['event_id'] == synthetic['id']]
    assert any(row['active'] for row in versions) and any(not row['active'] for row in versions)
    assert history.store.query('event', history.instance, {'provenance': 'Synthetic'})[1] == 2
