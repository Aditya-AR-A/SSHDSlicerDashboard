"""One scheduled recovery path for the sources used by every dashboard page.

The job collects evidence even without browser traffic. Status describes source
captures, not user activity or proof that the upstream inventory is complete.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import os
from uuid import uuid4

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.processing.workflow_history import WorkflowStore


SOURCES = ('configuration', 'daily', 'overview', 'efficiency', 'assignable_pool', 'assignments',
           'rework', 'pending_review', 'batches', 'returns', 'completion', 'workflow')
HEALTH_MAX_AGE_SECONDS = 600  # Two missed five-minute runs.
LEASE_SECONDS = 330  # Outlives a 300-second function; the next run can retry a killed job.
PROCESSOR_VERSION = 3


def utc_now():
    return datetime.now(timezone.utc)


class DataHealth:
    def __init__(self, manager, store=None):
        self.manager = manager
        settings = manager.settings
        configured = bool(settings.mongo_uri or settings.database_url)
        database = manager.db
        if store is None and configured and database.mongo_db is None and database.engine is None:
            raise ConnectionError('Configured capture storage unavailable')
        if store is None and os.getenv('VERCEL') and not configured:
            raise ConnectionError('Scheduled captures require shared database storage')
        self.store = store or WorkflowStore(database)
        # Isolate different source accounts sharing the same database.
        identity = f'{manager.scraper._base_url}:{settings.admin_username}'
        self.instance = 'data-health:' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.identifier = self.instance + ':status'

    def health(self):
        saved = self.store.get(self.identifier) or {}
        sources = {}
        now = utc_now()
        for name in SOURCES:
            item = dict(saved.get('sources', {}).get(name, {}))
            try:
                stamp = datetime.fromisoformat(item['last_success_at'])
                if stamp.tzinfo is None:
                    raise ValueError('Capture time must have a timezone')
                age = (now - stamp).total_seconds()
                recent = 0 <= age <= HEALTH_MAX_AGE_SECONDS
            except (KeyError, TypeError, ValueError):
                recent = False
            version_matches = item.get('processor_version') == PROCESSOR_VERSION
            build = os.getenv('VERCEL_GIT_COMMIT_SHA')
            version_matches = version_matches and (not build or item.get('build') == build)
            item['state'] = ('unavailable' if not item.get('last_success_at') else
                             'failed' if item.get('error') else 'fresh' if recent and version_matches else 'stale')
            sources[name] = item
        return {'ok': all(item['state'] == 'fresh' for item in sources.values()) and not saved.get('persistence_error'),
                'sources': sources, 'last_attempt_at': saved.get('last_attempt_at'),
                'finished_at': saved.get('finished_at'), 'storage': self.store.storage_label,
                'max_age_seconds': HEALTH_MAX_AGE_SECONDS, 'persistence_error': saved.get('persistence_error')}

    def _readers(self, start, end, today):
        manager = self.manager
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()

        def configuration():
            if not manager.refresh_shared_configuration():
                raise ConnectionError('Configuration unavailable')

        def daily():
            # Capture first: all subsequent sources can fail independently.
            for day in (today, yesterday):
                frame = manager.get_todays_work_df(day, force_refresh=True)
                if not frame.attrs.get('available') or any(frame.attrs.get(key) for key in
                        ('error', 'is_snapshot', 'persistence_error')):
                    raise ValueError('Daily capture unavailable or not persisted')

        def overview():
            if manager.fetch_dashboard_data(start, end, force_refresh=True).get('_source_error'):
                raise ValueError('Overview unavailable')

        def efficiency():
            summary, _ = manager.fetch_annotator_efficiency(start, end, force_refresh=True)
            if summary.get('_source_error'):
                raise ValueError('Efficiency unavailable')
            manager.capture_efficiency_history(yesterday, today, force_refresh=True)

        def pending():
            if manager.get_pending_review_df(force_refresh=True).attrs.get('pending_error'):
                raise ValueError('Pending review unavailable')

        def pool():
            if not manager.get_assignable_pool(force_refresh=True)['available']:
                raise ValueError('Assignable pool unavailable')

        def batches():
            manager.sync_batches_master(start, end, force_refresh=True)
            if getattr(manager, '_batch_source_error', None):
                raise ValueError('Batch history incomplete')

        def returns():
            manager.sync_batch_returns(force_refresh=True)
            if getattr(manager, '_returns_source_error', None):
                raise ValueError('Return history incomplete')

        def completion():
            from slicing_dashboard.reporting.completed_work import completed_work_for_period
            capture = completed_work_for_period(manager, start, end, force_refresh=True)
            if not capture.get('available') or capture.get('error') or capture.get('is_snapshot'):
                raise ValueError('Completion unavailable')

        def workflow():
            states = manager.get_workflow_data(force_refresh=True)['checkpoints']
            if len(states) != 3 or any(state.get('error') or not state.get('last_complete_at') for state in states):
                raise ValueError('Workflow scan incomplete; next run resumes checkpoints')

        return {'configuration': configuration, 'daily': daily,
                'overview': overview, 'efficiency': efficiency,
                'assignable_pool': pool,
                'assignments': lambda: manager._current_task_stats('slice_assigned', True),
                'rework': lambda: manager._current_task_stats('slice_rework', True),
                'pending_review': pending, 'batches': batches, 'returns': returns,
                'completion': completion, 'workflow': workflow}

    def refresh(self, selected=None):
        """Retry all or selected sources; each failure retains its last good time.

        A database lease prevents concurrent scheduled/manual jobs. If a job is
        killed, health ages out and the lease expires; a later run resumes.
        """
        selected = tuple(selected) if selected is not None else None
        refresh_configuration = selected is not None and 'configuration' in selected
        selected = selected if selected is not None else SOURCES
        if not selected or set(selected) - set(SOURCES):
            raise ValueError('Unknown or empty source selection')
        selected = tuple(dict.fromkeys(('configuration', *selected)))
        owner = uuid4().hex
        if not self.store.acquire_lease(self.instance, owner, seconds=LEASE_SECONDS):
            return {'busy': True, **self.health()}
        try:
            report = self.store.get(self.identifier) or {'sources': {}}
            report['last_attempt_at'] = utc_now().isoformat()
            report['sort_at'] = report['last_attempt_at']
            report['finished_at'] = None
            if not self.store.save_newer(self.identifier, 'data-health', report):
                raise TimeoutError('Newer capture already published')
            today = today_iso()
            # Load shared configuration before resolving period boundaries.
            self.manager.refresh_shared_configuration(force=refresh_configuration)
            periods = self.manager.get_available_periods()
            period = next((item for item in periods if item.get('is_current')), None)
            start = period['start_date'] if period else today[:8] + '01'
            end = min(period['end_date'], today) if period else today
            with self.manager.refresh_scope():
                for name, read in self._readers(start, end, today).items():
                    if name not in selected:
                        continue
                    item = dict(report['sources'].get(name, {}))
                    item.update(last_attempt_at=utc_now().isoformat(), error=None,
                                scope={'dates': [today, (date.fromisoformat(today) - timedelta(days=1)).isoformat()]}
                                if name == 'daily' else {'start': start, 'end': end}
                                if name in ('overview', 'efficiency', 'batches', 'completion') else {'current': True})
                    try:
                        read()
                        item['last_success_at'] = utc_now().isoformat()
                        item.update(processor_version=PROCESSOR_VERSION, build=os.getenv('VERCEL_GIT_COMMIT_SHA'))
                    except Exception as error:
                        item['error'] = type(error).__name__  # No URLs, credentials, task data.
                    lease = self.store.get('lease:' + self.instance)
                    if (not lease or lease.get('owner') != owner or
                            datetime.fromisoformat(lease['expires']) <= utc_now()):
                        raise TimeoutError('Capture lease expired; results not published')
                    report['sources'][name] = item
                    if not self.store.save_newer(self.identifier, 'data-health', report):
                        raise TimeoutError('Newer capture already published')
            persistence = getattr(self.manager, 'last_refresh_profile', {}).get('snapshot_persistence', {})
            if persistence.get('attempted'):
                shared = bool(self.manager.settings.mongo_uri or self.manager.settings.database_url)
                saved = persistence.get('database_saved' if shared else 'local_saved')
                report['persistence_error'] = None if saved else 'SnapshotPersistenceFailed'
            report['finished_at'] = utc_now().isoformat()
            if not self.store.save_newer(self.identifier, 'data-health', report):
                raise TimeoutError('Newer capture already published')
            health = self.health()
            return {'busy': False, **health,
                    'refresh_ok': all(health['sources'][name]['state'] == 'fresh' for name in selected)
                                  and not report.get('persistence_error')}
        finally:
            self.store.release_lease(self.instance, owner)


def register_data_health_routes(server, manager):
    """Protected operations endpoints; fail closed when CRON_SECRET is absent."""
    from flask import jsonify, request

    def authorized():
        secret = os.getenv('CRON_SECRET', '')
        return bool(secret) and hmac.compare_digest(request.headers.get('Authorization', '').encode(),
                                                   ('Bearer ' + secret).encode())

    def response(refresh=False):
        if not authorized():
            return jsonify(error='Unauthorized'), 401, {'Cache-Control': 'no-store'}
        try:
            service = DataHealth(manager)
            selected = request.args.get('sources')
            result = service.refresh(selected.split(',') if selected is not None else None) if refresh else service.health()
            if not refresh:
                from slicing_dashboard.reporting.refresh_jobs import RefreshJobs
                result['job'] = RefreshJobs(service).current()
                if result['job'] and result['job']['errors']:
                    result['ok'] = False
            status = 409 if result.get('busy') else 200 if result.get('refresh_ok', result['ok']) else 503
        except ValueError:
            result, status = {'error': 'Invalid source selection'}, 400
        except Exception as error:
            result, status = {'error': type(error).__name__}, 503
        return jsonify(result), status, {'Cache-Control': 'no-store'}

    server.add_url_rule('/api/data-health', 'data_health', lambda: response(), methods=['GET'])
    server.add_url_rule('/api/data-refresh', 'data_refresh', lambda: response(True), methods=['GET', 'POST'])

    def jobs_response(action):
        if not authorized():
            return jsonify(error='Unauthorized'), 401, {'Cache-Control': 'no-store'}
        try:
            from slicing_dashboard.reporting.refresh_jobs import RefreshJobs
            jobs = RefreshJobs(DataHealth(manager))
            if action == 'enqueue':
                sources = request.args.get('sources')
                result = {'job': jobs.enqueue(sources.split(',') if sources is not None else None)}
            else:
                payload = request.get_json(silent=True) or {}
                step = payload.get('step')
                if not isinstance(step, int) or isinstance(step, bool) or step < 0:
                    raise ValueError('Invalid step')
                identifier = payload.get('job_id')
                result = jobs.run(identifier, step) if action == 'run' else {'job': jobs.fail(identifier, step)}
            status = 200
        except BlockingIOError:
            result, status = {'error': 'Job checkpoint busy'}, 409
        except ValueError:
            result, status = {'error': 'Invalid job or source selection'}, 400
        except Exception as error:
            result, status = {'error': type(error).__name__}, 503
        return jsonify(result), status, {'Cache-Control': 'no-store'}

    for action in ('enqueue', 'run', 'fail'):
        server.add_url_rule('/api/data-jobs/' + action, 'data_jobs_' + action,
                            lambda action=action: jobs_response(action), methods=['POST'])
