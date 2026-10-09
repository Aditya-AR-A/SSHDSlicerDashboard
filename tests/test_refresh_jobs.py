"""Durability, concurrency and notification behavior for scheduled captures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest

from slicing_dashboard.processing.workflow_history import WorkflowHistory
from slicing_dashboard.reporting.refresh_jobs import RefreshJobs, utc_now
from slicing_dashboard.reporting.workflow_history import notice_link
from tests.test_data_health import service, successful_reads  # Shared isolated manager/store fixture.


def test_trigger_persists_without_reading_any_source_and_restart_resumes(service):
    with patch.object(service, 'refresh') as read:
        job = RefreshJobs(service).enqueue(['daily', 'assignable_pool'])
        read.assert_not_called()
    assert RefreshJobs(service).current() == job
    with patch.object(service, '_readers', return_value=successful_reads()):
        first = RefreshJobs(service).run(job['id'], 0)
        assert first['job']['state'] == 'queued' and first['job']['step'] == 1
        last = RefreshJobs(service).run(job['id'], 1)
    assert last['job']['state'] == 'completed'
    assert RefreshJobs(service).current()['finished_at']


def test_duplicate_triggers_coalesce_and_merge_independent_source_selection(service):
    jobs = RefreshJobs(service)
    first = jobs.enqueue(['daily'])
    duplicate = RefreshJobs(service).enqueue(['daily', 'assignable_pool'])
    assert first['id'] == duplicate['id']
    assert duplicate['sources'] == ['daily', 'assignable_pool']
    with patch.object(service, '_readers', return_value=successful_reads()):
        jobs.run(first['id'], 0)
        with patch.object(service, 'refresh') as read:
            assert jobs.run(first['id'], 0)['busy']
            read.assert_not_called()


def test_inflight_worker_is_not_duplicated_and_trigger_can_add_sources(service):
    from threading import Event
    started, finish = Event(), Event()
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily'])
    def read(_):
        started.set()
        assert finish.wait(5)
        return {'refresh_ok': True}
    with patch.object(service, 'refresh', side_effect=read), ThreadPoolExecutor() as pool:
        future = pool.submit(jobs.run, job['id'], 0)
        try:
            assert started.wait(5)
            assert jobs.run(job['id'], 0)['busy']
            assert jobs.enqueue(['daily', 'pending_review'])['id'] == job['id']
        finally:
            finish.set()
        result = future.result()
    assert result['job']['sources'] == ['daily', 'pending_review']
    assert result['job']['state'] == 'queued'


def test_source_failure_continues_other_plots_and_updates_one_read_safe_notice(service):
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily', 'pending_review', 'assignable_pool'])
    with patch.object(service, 'refresh', return_value={'refresh_ok': False}):
        result = jobs.run(job['id'], 0)
    assert result['job']['state'] == 'queued'
    inbox = WorkflowHistory(service.manager, service.store)
    notice = inbox.notifications()['rows'][0]
    assert notice['notification_type'] == 'refresh_failed'
    assert notice_link(notice) == '/reports/daily'
    inbox.mark_read(notice['id'])
    with patch.object(service, 'refresh', return_value={'refresh_ok': False}):
        jobs.run(job['id'], 1)
    assert inbox.notifications()['total'] == 1
    assert inbox.notifications()['unread'] == 0
    with patch.object(service, 'refresh', return_value={'refresh_ok': True}):
        result = jobs.run(job['id'], 2)
    assert result['job']['state'] == 'failed'
    assert set(result['job']['errors']) == {'daily', 'pending_review'}
    assert jobs.enqueue(['daily'])['id'] != job['id']


def test_expired_worker_is_detected_without_an_exception_handler(service):
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily', 'assignable_pool'])
    job.update(state='running', token='killed', deadline=(utc_now() - timedelta(seconds=1)).isoformat())
    jobs._save(job)
    restarted = RefreshJobs(service)
    current = restarted.current()
    assert current['step'] == 1 and current['state'] == 'queued'
    assert current['errors']['daily'] == 'WorkerTimeout'
    restarted.current()
    assert len(service.store.records('notification')) == 1


def test_dispatch_failure_and_late_worker_cannot_advance_twice(service):
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily', 'pending_review'])
    def old_worker(_):
        jobs.fail(job['id'], 0)
        return {'refresh_ok': True}
    with patch.object(service, 'refresh', side_effect=old_worker):
        result = jobs.run(job['id'], 0)
    assert result['busy']
    assert result['job']['step'] == 1
    assert jobs.fail(job['id'], 0)['step'] == 1


def test_busy_collector_requeues_without_failure_notification(service):
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily'])
    with patch.object(service, 'refresh', return_value={'busy': True}):
        result = jobs.run(job['id'], 0)
    assert result['busy'] and result['job']['state'] == 'queued'
    assert result['job']['step'] == 0 and not result['job']['errors']
    assert not service.store.records('notification')


def test_storage_outage_does_not_acknowledge_a_job(service):
    with patch.object(service.store, 'save', side_effect=ConnectionError('private database URI')):
        with pytest.raises(ConnectionError):
            RefreshJobs(service).enqueue(['daily'])
    assert RefreshJobs(service).current() is None


def test_exception_text_does_not_enter_status_or_notice(service):
    jobs = RefreshJobs(service)
    job = jobs.enqueue(['daily'])
    with patch.object(service, 'refresh', side_effect=RuntimeError('PRIVATE_SECRET')):
        result = jobs.run(job['id'], 0)
    assert result['job']['errors'] == {'daily': 'RuntimeError'}
    assert 'PRIVATE_SECRET' not in str(result) + str(service.store.records('notification'))


@pytest.mark.parametrize('sources', [[], ['unknown']])
def test_invalid_sources_do_not_enqueue(service, sources):
    with pytest.raises(ValueError):
        RefreshJobs(service).enqueue(sources)


def test_health_and_job_routes_protected_and_quick(service):
    from flask import Flask
    from slicing_dashboard.reporting.data_health import register_data_health_routes
    app = Flask(__name__)
    register_data_health_routes(app, service.manager)
    client = app.test_client()
    with patch.dict('os.environ', {'CRON_SECRET': 'test-secret'}), patch(
            'slicing_dashboard.reporting.data_health.DataHealth', return_value=service):
        for action in ('enqueue', 'run', 'fail'):
            assert client.post('/api/data-jobs/' + action).status_code == 401
        headers = {'Authorization': 'Bearer test-secret'}
        with patch.object(service, 'refresh') as read:
            queued = client.post('/api/data-jobs/enqueue?sources=daily', headers=headers)
            assert queued.status_code == 200
            read.assert_not_called()
        job = queued.json['job']
        assert client.post('/api/data-jobs/run', headers=headers, json={'job_id': job['id'], 'step': '0'}).status_code == 400
        with patch.object(service, 'refresh', return_value={'refresh_ok': False}):
            assert client.post('/api/data-jobs/run', headers=headers, json={'job_id': job['id'], 'step': 0}).status_code == 200
        health = client.get('/api/data-health', headers=headers)
        assert health.status_code == 503 and health.json['job']['state'] == 'failed'
