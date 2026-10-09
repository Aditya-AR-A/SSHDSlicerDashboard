"""Recovery, expiry and worker configuration without production services."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask
import pandas as pd
import pytest

from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.processing.workflow_history import WorkflowStore
from slicing_dashboard.reporting.data_health import DataHealth, SOURCES, register_data_health_routes
from slicing_dashboard.reporting.dashboard_reports import day_for_ui


def manager():
    result = MagicMock()
    result.settings = SimpleNamespace(mongo_uri=None, database_url=None, admin_username='test')
    result.scraper._base_url = 'https://source.example'
    result.db = SimpleNamespace(mongo_db=None, engine=None)
    result.refresh_scope.side_effect = lambda: nullcontext()
    result.refresh_shared_configuration.return_value = True
    result.get_available_periods.return_value = [{'start_date': '2026-10-01', 'end_date': '2026-10-08', 'is_current': True}]
    frame = pd.DataFrame()
    frame.attrs['available'] = True
    result.get_todays_work_df.return_value = frame
    result.fetch_dashboard_data.return_value = {}
    result.fetch_annotator_efficiency.return_value = ({}, [])
    result.get_pending_review_df.return_value = pd.DataFrame()
    result.get_assignable_pool.return_value = {'available': True, 'duration_seconds': 0, 'task_count': 0}
    result._batch_source_error = None
    result._returns_source_error = None
    result.get_workflow_data.return_value = {'checkpoints': [{'last_complete_at': '2026-10-08'}] * 3}
    result.last_refresh_profile = {}
    return result


@pytest.fixture
def service(tmp_path):
    with patch('slicing_dashboard.reporting.data_health.today_iso', return_value='2026-10-08'):
        yield DataHealth(manager(), WorkflowStore(path=tmp_path / 'health.sqlite3'))


def successful_reads():
    return {name: lambda: None for name in SOURCES}


def test_independent_failures_retry_and_worker_restart(service):
    with patch.object(service, '_readers', return_value=successful_reads()):
        assert service.refresh()['ok']
    before = service.health()['sources']['daily']['last_success_at']
    reads = successful_reads()
    reads['daily'] = MagicMock(side_effect=RuntimeError('Secret upstream URL must not leak'))
    reads['pending_review'] = MagicMock()
    with patch.object(service, '_readers', return_value=reads):
        failed = service.refresh()
    assert not failed['ok']
    assert failed['sources']['daily']['state'] == 'failed'
    assert failed['sources']['daily']['last_success_at'] == before
    assert failed['sources']['daily']['error'] == 'RuntimeError'
    reads['pending_review'].assert_called_once()
    restarted = DataHealth(service.manager, service.store)
    assert restarted.health()['sources'] == failed['sources']
    with patch.object(restarted, '_readers', return_value=successful_reads()):
        assert restarted.refresh(['daily'])['ok']


def test_success_time_expires_and_new_build_requires_new_capture(service):
    with patch.object(service, '_readers', return_value=successful_reads()):
        assert service.refresh()['ok']
    with patch('slicing_dashboard.reporting.data_health.utc_now',
               return_value=datetime.now(timezone.utc) + timedelta(seconds=601)):
        assert all(row['state'] == 'stale' for row in service.health()['sources'].values())
    with patch.dict('os.environ', {'VERCEL_GIT_COMMIT_SHA': 'new-build'}):
        assert not service.health()['ok']
        assert all(row['state'] == 'stale' for row in service.health()['sources'].values())


def test_concurrent_invocations_do_not_start_another_scan(service):
    assert service.store.acquire_lease(service.instance, 'other', seconds=300)
    with patch.object(service, '_readers') as readers:
        assert service.refresh()['busy']
        readers.assert_not_called()
    service.store.release_lease(service.instance, 'other')
    with patch.object(service, '_readers', return_value=successful_reads()):
        assert service.refresh()['ok']


def test_database_rejects_a_slow_checkpoint_after_a_newer_worker_publishes(service):
    first = {'sort_at': '2026-10-08T01:00:00+00:00', 'sources': {'daily': {'error': 'old'}}}
    second = {'sort_at': '2026-10-08T01:01:00+00:00', 'sources': {'daily': {'error': None}}}
    assert service.store.save_newer(service.identifier, 'data-health', first)
    assert service.store.save_newer(service.identifier, 'data-health', second)
    assert not service.store.save_newer(service.identifier, 'data-health', first)
    assert service.store.get(service.identifier) == second


def test_expired_old_job_cannot_publish_health_over_new_job(service):
    def takeover():
        lease = service.store.get('lease:' + service.instance)
        service.store.release_lease(service.instance, lease['owner'])
        assert service.store.acquire_lease(service.instance, 'new-job', seconds=300)
        service.store.save(service.identifier, 'data-health', {'sources': {}, 'finished_at': 'new-job'}, replace=True)
    with patch.object(service, '_readers', return_value={'daily': takeover}):
        with pytest.raises(TimeoutError):
            service.refresh(['daily'])
    assert service.store.get(service.identifier)['finished_at'] == 'new-job'
    assert service.store.get('lease:' + service.instance)['owner'] == 'new-job'


def test_all_plot_sources_forced_and_midnight_uses_two_dated_captures(service):
    with patch('slicing_dashboard.reporting.completed_work.completed_work_for_period',
               return_value={'available': True, 'is_snapshot': False}) as completion:
        result = service.refresh()
    assert result['ok']
    assert [call.args[0] for call in service.manager.get_todays_work_df.call_args_list] == ['2026-10-08', '2026-10-07']
    assert all(call.kwargs['force_refresh'] for call in service.manager.get_todays_work_df.call_args_list)
    service.manager.fetch_dashboard_data.assert_called_with('2026-10-01', '2026-10-08', force_refresh=True)
    service.manager.get_pending_review_df.assert_called_with(force_refresh=True)
    service.manager.get_assignable_pool.assert_called_with(force_refresh=True)
    assert {call.args for call in service.manager._current_task_stats.call_args_list} == {
        ('slice_assigned', True), ('slice_rework', True)}
    completion.assert_called_with(service.manager, '2026-10-01', '2026-10-08', force_refresh=True)


@pytest.mark.parametrize('failure', ['is_snapshot', 'persistence_error', 'error'])
def test_saved_or_unsaved_daily_capture_is_not_reported_healthy(service, failure):
    service.manager.get_todays_work_df.return_value.attrs[failure] = True
    result = service.refresh(['daily'])
    assert not result['refresh_ok']
    assert result['sources']['daily']['state'] == 'unavailable'


def test_snapshot_persistence_failure_is_not_hidden_by_successful_api_reads(service):
    service.manager.last_refresh_profile = {'snapshot_persistence': {'attempted': True, 'local_saved': False}}
    with patch.object(service, '_readers', return_value=successful_reads()):
        result = service.refresh()
    assert not result['ok'] and not result['refresh_ok']
    assert result['persistence_error'] == 'SnapshotPersistenceFailed'


def test_operations_endpoints_auth_scope_and_http_status(service):
    server = Flask(__name__)
    register_data_health_routes(server, service.manager)
    client = server.test_client()
    with patch.dict('os.environ', {'CRON_SECRET': ''}):
        assert client.get('/api/data-health').status_code == 401
    with patch.dict('os.environ', {'CRON_SECRET': 'test-secret'}), patch(
            'slicing_dashboard.reporting.data_health.DataHealth', return_value=service):
        assert client.post('/api/data-refresh').status_code == 401
        headers = {'Authorization': 'Bearer test-secret'}
        assert client.get('/api/data-health', headers=headers).status_code == 503
        assert client.post('/api/data-refresh?sources=bad', headers=headers).status_code == 400
        with patch.object(service, '_readers', return_value=successful_reads()):
            response = client.post('/api/data-refresh?sources=daily', headers=headers)
            assert response.status_code == 200
            assert response.json['refresh_ok'] and not response.json['ok']
            assert client.get('/api/data-health', headers=headers).status_code == 503
            assert client.post('/api/data-refresh', headers=headers).status_code == 200
        assert client.get('/api/data-health', headers=headers).status_code == 200
        assert client.get('/api/data-health', headers=headers).headers['Cache-Control'] == 'no-store'
        assert service.store.acquire_lease(service.instance, 'other', seconds=300)
        assert client.post('/api/data-refresh', headers=headers).status_code == 409


def test_vercel_cannot_silently_use_ephemeral_local_storage():
    with patch.dict('os.environ', {'VERCEL': '1'}), pytest.raises(ConnectionError):
        DataHealth(manager())


def test_database_outage_can_recover_without_restarting_any_plot_worker():
    database = DatabaseManager.__new__(DatabaseManager)
    database._connected = None
    database.mongo_client = MagicMock()
    database.mongo_db = object()
    database.engine = None
    database.mongo_client.admin.command.side_effect = [ConnectionError('temporary outage'), {'ok': 1}]
    with patch('slicing_dashboard.db.perf_counter', return_value=100):
        assert not database.is_connected()
    with patch('slicing_dashboard.db.perf_counter', return_value=159):
        assert not database.is_connected()
        assert database.mongo_client.admin.command.call_count == 1
    with patch('slicing_dashboard.db.perf_counter', return_value=161):
        assert database.is_connected()
        assert database.mongo_client.admin.command.call_count == 2


def test_targeted_retry_does_not_ignore_failed_configuration(service):
    service.manager.refresh_shared_configuration.return_value = False
    result = service.refresh(['daily'])
    assert not result['refresh_ok']
    assert result['sources']['configuration']['error'] == 'ConnectionError'
    assert result['sources']['daily']['state'] == 'fresh'
    assert service.manager.refresh_shared_configuration.call_args_list[0].kwargs == {'force': False}


def test_explicit_configuration_retry_can_reload_shared_mappings(service):
    service.refresh(['configuration'])
    assert service.manager.refresh_shared_configuration.call_args_list[0].kwargs == {'force': True}


def test_mapping_change_invalidates_every_derived_cache_and_failure_preserves_mapping():
    dm = DataManager.__new__(DataManager)
    dm.settings = SimpleNamespace(mongo_uri='configured', database_url=None)
    dm.db = MagicMock(mongo_db=object())
    dm.db.load_user_mappings.return_value = [{'id': 'A', 'mapped_user': 'Aditya', 'mapping_type': 'Existing'}]
    dm.db.load_settlement_periods.return_value = []
    dm.user_mapping = {'A': 'Wrong name'}
    dm.user_mapping_full = {'A': {'id': 'A', 'mapped_user': 'Wrong name'}}
    dm._daily_payload_cache = {'old': object()}
    dm._current_queue_cache = {'slice_assigned': object()}
    dm.invalidate_pending_review = MagicMock()
    assert dm.refresh_shared_configuration()
    assert dm.user_mapping == {'A': 'Aditya'}
    assert not dm._daily_payload_cache and not dm._current_queue_cache
    dm.invalidate_pending_review.assert_called_once()
    assert dm.refresh_shared_configuration()
    assert dm.db.load_user_mappings.call_count == 1
    dm.db.load_user_mappings.side_effect = ConnectionError('Private credentials')
    assert not dm.refresh_shared_configuration(force=True)
    assert dm.user_mapping == {'A': 'Aditya'}
    assert dm._configuration_error == 'ConnectionError'


def test_midnight_does_not_turn_an_afternoon_capture_into_a_final_total():
    record = {'date': '2026-10-07', 'rows': [], 'metadata': {'available': True, 'closed': True,
              'captured_at': '2026-10-07T16:00:00+05:30', 'capture_kind': 'daily_observation'}}
    with patch('slicing_dashboard.management.periods.today_iso', return_value='2026-10-08'):
        assert day_for_ui(record, '2026-10-07')['metadata']['reconciliation_pending']
        record['metadata']['day_end_reconciled_at'] = '2026-10-08T00:30:00+05:30'
        assert not day_for_ui(record, '2026-10-07')['metadata']['reconciliation_pending']


def test_mapping_health_does_not_repeat_dashboard_prompts():
    from tests.test_daily_report_page import application_namespace, payload_text
    namespace, _ = application_namespace()
    status = {'is_live': True, 'is_using_snapshot': False, 'configuration_error': 'ConnectionError'}
    assert 'Mappings unverified' not in payload_text(namespace['_build_status_badge'](status))
    assert namespace['_build_status_banner'](status) is None


def test_known_accounts_do_not_reload_mappings_but_new_accounts_do():
    dm = DataManager.__new__(DataManager)
    dm.settings = SimpleNamespace(mongo_uri='configured', database_url=None)
    dm.db = MagicMock(mongo_db=object())
    dm.db.load_user_mappings.return_value = [{'id': 'SSHD-A', 'mapped_user': 'Aditya'}]
    dm.db.load_settlement_periods.return_value = []
    dm.user_mapping = {'SSHD-A': 'Aditya'}
    dm.user_mapping_full = {'SSHD-A': {'id': 'SSHD-A', 'mapped_user': 'Aditya'}}
    dm.scraper = MagicMock()
    dm.scraper._users = {1: {'username': 'SSHD-A'}}
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=100):
        assert dm.refresh_shared_configuration()
    # Even expired configuration checks reuse mappings for known accounts.
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=161):
        assert dm.refresh_shared_configuration()
    assert dm.db.load_user_mappings.call_count == 1
    assert dm.db.load_settlement_periods.call_count == 2
    assert dm._get_canonical_name(2, 'SSHD-New') == 'SSHD-New'
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=222):
        assert dm.refresh_shared_configuration()
    assert dm.db.load_user_mappings.call_count == 2
    with patch('slicing_dashboard.data_manager.perf_counter', return_value=283):
        assert dm.refresh_shared_configuration()
    assert dm.db.load_user_mappings.call_count == 2
    dm.scraper._fetch_users.assert_not_called()
    # A deliberate admin read can pick up assignments saved by another worker.
    dm.db.load_user_mappings.return_value += [{'id': 'SSHD-New', 'mapped_user': 'Riya'}]
    assert dm.refresh_shared_configuration(force=True)
    assert dm._get_canonical_name(2, 'SSHD-New') == 'Riya'


def test_cached_workflow_service_does_not_wait_for_an_active_projection():
    from concurrent.futures import ThreadPoolExecutor
    from slicing_dashboard.processing.workflow_history import LOCK

    dm = DataManager.__new__(DataManager)
    dm._workflow_history_service = object()
    # A collector owns the shared lock in another thread. Cached lookup must
    # still complete, so independently rendered inbox/history reads can proceed.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with LOCK:
            lookup = pool.submit(dm._workflow_history)
            result = lookup.result(timeout=1)
        assert result is dm._workflow_history_service


def test_stalled_established_mongo_write_times_out_and_next_write_recovers(monkeypatch):
    """Exercise the real driver's operation deadline, after a successful handshake."""
    import socket
    import struct
    from threading import Event, Thread
    from time import perf_counter
    from bson import BSON
    from pymongo.errors import PyMongoError

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(.1)
    stopped, seen_write, allow_writes = Event(), Event(), Event()
    connections = []
    hello = {'ok': 1, 'ismaster': True, 'isWritablePrimary': True,
             'minWireVersion': 0, 'maxWireVersion': 17,
             'maxBsonObjectSize': 16777216, 'maxMessageSizeBytes': 48000000,
             'maxWriteBatchSize': 100000}

    def receive(connection, size):
        data = b''
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise OSError('Connection closed')
            data += chunk
        return data

    def serve(connection):
        try:
            while not stopped.is_set():
                length, request_id, _, opcode = struct.unpack('<iiii', receive(connection, 16))
                body = receive(connection, length - 16)
                if opcode == 2004:  # Initial legacy hello query.
                    payload = struct.pack('<iqii', 0, 0, 0, 1) + BSON.encode(hello)
                    reply_opcode = 1
                else:
                    command = BSON(body[5:5 + struct.unpack('<i', body[5:9])[0]]).decode()
                    if 'update' in command:
                        seen_write.set()
                        while not allow_writes.wait(.05):
                            if stopped.is_set():
                                return
                    result = hello if 'hello' in command or 'ismaster' in command else {'ok': 1, 'n': 1, 'nModified': 1}
                    payload = struct.pack('<I', 0) + b'\x00' + BSON.encode(result)
                    reply_opcode = 2013
                connection.sendall(struct.pack('<iiii', len(payload) + 16, 1, request_id, reply_opcode) + payload)
        except OSError:
            pass
        finally:
            connection.close()

    def accept():
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connections.append(connection)
            Thread(target=serve, args=(connection,), daemon=True).start()

    Thread(target=accept, daemon=True).start()
    settings = SimpleNamespace(mongo_uri=f'mongodb://127.0.0.1:{listener.getsockname()[1]}/?directConnection=true',
                               database_url=None)
    monkeypatch.setattr('slicing_dashboard.db.get_settings', lambda: settings)
    monkeypatch.setattr('slicing_dashboard.db.MONGO_OPERATION_TIMEOUT_MS', 500)
    database = DatabaseManager()
    try:
        store = WorkflowStore(database)
        started = perf_counter()
        with pytest.raises(PyMongoError) as error:
            store.save_many('event', [{'id': 'bounded-write', 'instance': 'test'}])
        assert seen_write.is_set(), 'Must test an established write, not a failed connection'
        assert error.value.timeout
        assert perf_counter() - started < 3
        allow_writes.set()
        store.save_many('event', [{'id': 'recovered-write', 'instance': 'test'}])
    finally:
        stopped.set()
        allow_writes.set()
        database.mongo_client.close()
        listener.close()
        for connection in connections:
            connection.close()
