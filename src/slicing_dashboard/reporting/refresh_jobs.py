"""Durable capture jobs; each worker invocation collects one source.

The scheduler acknowledges persisted work, not completed work. No Python thread
is detached from a serverless request. Expired invocations are reconciled by the
next trigger or independent health check.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from uuid import uuid4

from slicing_dashboard.reporting.data_health import SOURCES, utc_now

STEP_SECONDS = 330
DISPATCH_SECONDS = 600


class RefreshJobs:
    def __init__(self, health):
        self.health = health
        self.store = health.store
        self.instance = health.instance + ':jobs'
        self.pointer = self.instance + ':active'

    @contextmanager
    def _lock(self):
        owner = uuid4().hex
        if not self.store.acquire_lease(self.instance, owner, seconds=60):
            raise BlockingIOError('Job checkpoint busy')
        try:
            yield
        finally:
            self.store.release_lease(self.instance, owner)

    def _save(self, job):
        job['sort_at'] = utc_now().isoformat()
        self.store.save(job['id'], 'refresh-job', job, replace=True)

    def _notice(self, job):
        # Share the existing inbox and its durable read records. Updating an
        # attempt's notice never duplicates it or marks it unread again.
        instance = hashlib.sha256(self.health.manager.scraper._base_url.rstrip('/').encode()).hexdigest()[:16]
        stamp = utc_now().isoformat()
        identifier = job['id'] + ':failure'
        previous = self.store.get(identifier) or {}
        labels = ', '.join(name.replace('_', ' ') for name in job['errors'])
        self.store.save(identifier, 'notification', {
            'id': identifier, 'instance': instance, 'event_id': job['id'],
            'notification_type': 'refresh_failed', 'category': 'danger',
            'title': 'Dashboard refresh failed', 'member': 'Dashboard',
            'actor': 'Scheduled capture', 'stage': 'Data refresh',
            'reason': f'Could not refresh {labels}. These values may be out of date. The next scheduled run will retry.',
            'event_at': previous.get('event_at', stamp),
            'observed_at': previous.get('observed_at', stamp),
            'sort_at': previous.get('sort_at', stamp), 'active': True,
            'recipient_scope': 'shared-team', 'provenance': 'Capture failure',
        }, replace=True)

    def _advance(self, job, error=None):
        source = job['sources'][job['step']]
        if error:
            job['errors'][source] = error
        job['step'] += 1
        job['state'] = ('failed' if job['errors'] else 'completed') if job['step'] == len(job['sources']) else 'queued'
        job['deadline'] = (utc_now() + timedelta(seconds=DISPATCH_SECONDS)).isoformat()
        job.pop('token', None)
        if job['state'] in ('completed', 'failed'):
            job['finished_at'] = utc_now().isoformat()
        self._save(job)
        if job['errors']:
            self._notice(job)
        return job

    def _current(self):
        pointer = self.store.get(self.pointer)
        job = self.store.get(pointer['job_id']) if pointer else None
        if job and job['state'] in ('queued', 'running') and datetime.fromisoformat(job['deadline']) <= utc_now():
            self._advance(job, 'WorkerTimeout' if job['state'] == 'running' else 'DispatchTimeout')
        # Reconcile a notice write interrupted by a database outage.
        if job and job['errors']:
            self._notice(job)
        return job

    def current(self):
        with self._lock():
            return self._current()

    def enqueue(self, sources=None):
        sources = tuple(dict.fromkeys(sources if sources is not None else SOURCES))
        if not sources or set(sources) - set(SOURCES):
            raise ValueError('Invalid source selection')
        with self._lock():
            current = self._current()
            if current and current['state'] in ('queued', 'running'):
                # Do not silently drop an independently requested source.
                missing = [source for source in sources if source not in current['sources']]
                if missing:
                    current['sources'].extend(missing)
                    self._save(current)
                return current
            stamp = utc_now()
            job = {'id': self.instance + ':' + uuid4().hex, 'instance': self.instance,
                   'sources': list(sources), 'step': 0, 'state': 'queued', 'errors': {},
                   'created_at': stamp.isoformat(),
                   'deadline': (stamp + timedelta(seconds=DISPATCH_SECONDS)).isoformat()}
            self._save(job)
            self.store.save(self.pointer, 'refresh-job-pointer', {'job_id': job['id']}, replace=True)
            return job

    def run(self, identifier, expected_step):
        with self._lock():
            job = self._current()
            if not job or job['id'] != identifier:
                raise ValueError('Unknown active job')
            if job['state'] != 'queued' or job['step'] != expected_step:
                return {'busy': True, 'job': job}
            token = uuid4().hex
            job.update(state='running', token=token,
                       deadline=(utc_now() + timedelta(seconds=STEP_SECONDS)).isoformat())
            self._save(job)
            source = job['sources'][job['step']]
        try:
            result = self.health.refresh([source])
            error = None if result.get('refresh_ok') else 'SourceCaptureFailed'
        except Exception as exc:
            result, error = {}, type(exc).__name__  # Never persist exception text/secrets.
        with self._lock():
            current = self._current()
            if not current or current['id'] != identifier or current.get('token') != token:
                return {'busy': True, 'job': current}  # An expired worker cannot advance again.
            if result.get('busy'):
                current.update(state='queued', deadline=(utc_now() + timedelta(seconds=DISPATCH_SECONDS)).isoformat())
                current.pop('token', None)
                self._save(current)
                return {'busy': True, 'job': current}
            return {'busy': False, 'job': self._advance(current, error)}

    def fail(self, identifier, expected_step):
        """Called by the background dispatcher when a worker connection fails."""
        with self._lock():
            job = self._current()
            if not job or job['id'] != identifier:
                raise ValueError('Unknown active job')
            if job['step'] == expected_step and job['state'] in ('running', 'queued'):
                self._advance(job, 'WorkerConnectionFailed')
            return job
