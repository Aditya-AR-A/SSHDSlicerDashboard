from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import dash
import pytest

from tests.test_daily_report_page import application_namespace, components, payload_text
from tests.test_workflow_history import review, completed, request, evidence, service, NOW, INSTANCE
from slicing_dashboard.processing.workflow_events import project_events, project_notices
from slicing_dashboard.pages.workflow_history import notification_updates, notification_toasts


def test_settings_route_moves_editors_and_keeps_their_instances():
    namespace, manager = application_namespace()
    nodes = {getattr(node, 'id', None): node for node in components(namespace['app'].layout)}
    dashboard_ids = {getattr(node, 'id', None) for node in components(nodes['dashboard-page'])}
    assert 'mapping-auth-input' not in dashboard_ids and 'settlement-auth-input' not in dashboard_ids
    assert 'mapping-auth-input' in {getattr(node, 'id', None) for node in components(nodes['settings-page'])}
    assert 'tabs' not in nodes and 'tabs-content' not in nodes
    for tab in ('settings-settlement', 'settings-mapping', 'settings-settlement'):
        styles = namespace['show_settings_tab'](tab)
        assert styles[0 if tab == 'settings-settlement' else 1] == {}
    route = namespace['route_pages']('/settings')
    assert route[11] == {} and route[12] and route[2] == {'display': 'none'}
    assert route[0] == {'display': 'none'} and route[5] == {'display': 'none'}
    editors = (nodes['settings-settlement-panel'].children, nodes['settings-mapping-panel'].children)
    for theme in (0, 1):
        output = namespace['update_dashboard'](1, 0, '2026-10-01', '2026-10-07', None, theme,
                    'tab-overview', None, None, '/settings')
        assert all(value is dash.no_update for value in output)
    assert editors == (nodes['settings-settlement-panel'].children, nodes['settings-mapping-panel'].children)
    manager.fetch_dashboard_data.assert_not_called()
    manager.get_settlement_df.assert_not_called()


def test_admin_approval_is_a_notification_without_requests():
    events = project_events([review('admin', status='batch_completed')], [], [], INSTANCE, NOW)
    notices = project_notices([], INSTANCE, events)
    assert len(notices) == 1
    notice = notices[0]
    assert notice['title'] == 'Batch approved by Admin'
    assert notice['category'] == 'success' and notice['actor_id'] == 5
    assert notice['event_id'].endswith('admin:action')
    assert notice['event_at'] and notice['stage'] == 'Admin'


@pytest.mark.parametrize('previous,stage', [('batch_reviewing', 'Leader'),
    ('batch_pending_auditor_review', 'Auditor'), ('batch_pending_admin_review', 'Admin')])
def test_returns_by_every_reviewer_generate_notifications(previous, stage):
    observed = [review('returned', decision='returned', status='batch_rework', review_previous_status=previous)]
    events = project_events(observed, [], [], INSTANCE, NOW)
    notices = project_notices([], INSTANCE, events)
    assert len(notices) == 1 and notices[0]['notification_type'] == 'batch_returned'
    assert notices[0]['stage'] == stage and notices[0]['category'] == 'warning'


def test_completion_notice_is_stable_when_real_admin_evidence_arrives(tmp_path):
    history, _ = service(tmp_path, requests=[])
    history.manager._batches_master_cache = {'ab_example': completed()}
    history.sync(force=True)
    notice = history.notifications()['rows'][0]
    assert notice['provenance'] == 'Completion inferred' and notice['event_at'] is None
    history.mark_read(notice['id'])
    observation = evidence('batch-review', review('actual-admin', status='batch_completed')['raw'], instance=history.instance)
    history.store.save(observation['id'], 'observation', observation)
    history.capture_batches()
    updated = history.notifications()
    assert updated['total'] == 1 and updated['unread'] == 0
    assert updated['rows'][0]['id'] == notice['id'] and updated['rows'][0]['event_at']
    assert updated['rows'][0]['title'] == 'Batch approved by Admin'


def test_matching_request_and_review_return_share_one_notice_id():
    observed = evidence('request', request('return', kind='rework_notice', returned_by=5), 'created')
    events = project_events([review('returned', decision='returned', status='batch_rework')], [], [], INSTANCE, NOW)
    notices = project_notices([observed], INSTANCE, events)
    assert len(notices) == 1
    assert notices[0]['id'] == observed['event_key'] + ':notice'
    assert notices[0]['notification_type'] == 'batch_returned'


def test_mark_all_read_is_persistent_and_source_scoped(tmp_path):
    history, _ = service(tmp_path)
    history.sync(force=True)
    notice = history.notifications()['rows'][0]
    history.store.save('other-notice', 'notification', {**notice, 'instance': 'other', 'id': 'other-notice'})
    assert history.mark_all_read()['unread'] == 0
    assert history.store.get('read:other-notice') is None
    history.sync(force=True)
    assert history.notifications()['unread'] == 0


def test_late_request_notice_enriches_existing_read_batch_return(tmp_path):
    history, requests = service(tmp_path, requests=[])
    observation = evidence('batch-review', review('return-first', decision='returned', status='batch_rework')['raw'], instance=history.instance)
    history.store.save(observation['id'], 'observation', observation)
    history.sync(force=True)
    notice = history.notifications()['rows'][0]
    history.mark_read(notice['id'])
    requests.append(request('late-request', kind='rework_notice', returned_by=5))
    history.sync(force=True)
    inbox = history.notifications()
    assert inbox['total'] == 1 and inbox['unread'] == 0
    assert inbox['rows'][0]['id'] == notice['id']


def test_notifications_poll_on_settings_and_toast_clicks_open_history():
    namespace, manager = application_namespace()
    with patch('dash.ctx', SimpleNamespace(triggered_id='workflow-notification-interval')):
        namespace['load_workflow_data']('/settings', 0, 0, 0, 1)
    manager.get_workflow_data.assert_called_once_with(force_refresh=True)
    with patch('dash.ctx', SimpleNamespace(triggered_id={'type': 'workflow-toast-notice', 'id': 'new'})):
        manager.mark_workflow_notification_read.return_value = ('/workflow?batch=ab_example', {})
        assert namespace['read_workflow_notification']([], [1])[0] == '/workflow?batch=ab_example'
    manager.mark_all_workflow_notifications_read.side_effect = ConnectionError('private')
    assert 'private' not in str(namespace['read_all_workflow_notifications'](1))
    manager.mark_all_workflow_notifications_read.side_effect = None
    first = namespace['read_all_workflow_notifications'](1)
    second = namespace['read_all_workflow_notifications'](2)
    assert first['change_id'] != second['change_id']
    manager._workflow_history.return_value.notifications.return_value = {'rows': [], 'total': 2, 'unread': 0}
    rendered = namespace['render_workflow_notifications']({'inbox': {'rows': [], 'unread': 2}}, second)
    assert rendered[1:] == ('0', True)


def test_initial_history_and_old_backfill_do_not_pop_up_but_new_events_do():
    now = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    notices = [{'id': 'old', 'event_at': '2026-10-01T10:00:00Z', 'observed_at': NOW}]
    data = {'inbox': {'rows': notices}}
    updates, seen = notification_updates(data, now=now)
    assert updates == []
    new = {'id': 'new', 'member': 'Member', 'title': 'Batch approved by Admin', 'category': 'success',
           'event_at': '2026-10-07T10:01:00Z', 'observed_at': '2026-10-07T10:01:10Z'}
    backfill = {'id': 'backfill', 'event_at': '2026-07-01T10:00:00Z', 'observed_at': new['observed_at']}
    data['inbox']['rows'] = [new, backfill, *notices]
    updates, seen = notification_updates(data, seen)
    assert updates == [new]
    assert notification_updates(data, seen)[0] == []
    assert notification_updates({'error': 'Offline'}, seen) == ([], seen)
    toast = notification_toasts(updates)[0]
    assert toast.is_open and toast.duration == 10000
    assert 'workflow-toast-notice' in payload_text(toast)
