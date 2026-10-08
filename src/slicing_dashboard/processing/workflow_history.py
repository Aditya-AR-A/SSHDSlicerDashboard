"""Durable source evidence, workflow projections and a shared notification inbox.

Business-rule milestones are explicitly synthetic. Source notices can generate
inbox entries; neither inferred prerequisites nor unknown event dates are hours.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock

from slicing_dashboard.processing.daily_work import instant
from slicing_dashboard.processing.daily_work_source import DailyWorkSource

LOCK = RLock()
ENDPOINTS = {
    "batch-review": "/api/review/reviewed-batches",
    "task-review": "/api/review/reviewed-tasks",
    "request": "/api/requests",
}
# Deliberate allowlist: no user profiles, contact information or auth payloads.
FIELDS = set("id assignment_batch_id task_id task_desensitize_id workflow_type "
             "review_log_id reviewed_at review_action review_decision review_previous_status "
             "review_current_status review_comment reviewer_id reviewer_username "
             "assignee_id assignee_username slicer_id slicer request_type status "
             "requester_id requester_name returned_by returned_by_name decided_by "
             "created_at decided_at decision_note reason batch_status".split())


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class WorkflowStore:
    """Use configured Mongo/PG storage, or persistent SQLite for local installs.

    Configured database failures propagate; do not silently create a divergent
    inbox/history on a serverless instance. Source versions are insert-only.
    """
    def __init__(self, database=None, path=None):
        self.mongo = getattr(database, "mongo_db", None)
        self.engine = getattr(database, "engine", None)
        if path is None:
            from slicing_dashboard.config import DATA_DIR
            path = DATA_DIR / 'reports' / 'workflow.sqlite3'
        self.path = Path(path)
        if self.mongo is None and self.engine is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._sqlite() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS workflow_records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)")
        elif self.engine is not None:
            from sqlalchemy import text
            with self.engine.begin() as conn:
                conn.execute(text("CREATE TABLE IF NOT EXISTS workflow_records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)"))
        if self.mongo is not None:
            self.mongo['workflow_records'].create_index([('kind', 1), ('instance', 1), ('sort_at', -1)])
        elif self.engine is not None:
            from sqlalchemy import text
            with self.engine.begin() as conn:
                conn.execute(text('CREATE INDEX IF NOT EXISTS workflow_kind_idx ON workflow_records (kind)'))
                conn.execute(text("CREATE INDEX IF NOT EXISTS workflow_lookup_idx ON workflow_records "
                                  "(kind, ((payload::jsonb ->> 'instance')), ((payload::jsonb ->> 'sort_at')) DESC)"))
        else:
            with self._sqlite() as conn:
                conn.execute('CREATE INDEX IF NOT EXISTS workflow_kind_idx ON workflow_records (kind)')
                conn.execute("CREATE INDEX IF NOT EXISTS workflow_lookup_idx ON workflow_records "
                             "(kind, json_extract(payload, '$.instance'), json_extract(payload, '$.sort_at') DESC)")

    @property
    def storage_label(self):
        return "MongoDB" if self.mongo is not None else "PostgreSQL" if self.engine is not None else "Local SQLite (this server)"

    def _sqlite(self):
        return sqlite3.connect(self.path, timeout=20)

    def save(self, identifier, kind, payload, *, replace=False):
        record = self._record(identifier, kind, payload)
        if self.mongo is not None:
            operator = "$set" if replace else "$setOnInsert"
            self.mongo["workflow_records"].update_one({"_id": identifier}, {operator: record}, upsert=True)
        elif self.engine is not None:
            from sqlalchemy import text
            conflict = "DO UPDATE SET payload=EXCLUDED.payload" if replace else "DO NOTHING"
            with self.engine.begin() as conn:
                conn.execute(text("INSERT INTO workflow_records (id,kind,payload) VALUES (:id,:kind,:payload) ON CONFLICT (id) " + conflict), record)
        else:
            with self._sqlite() as conn:
                verb = "REPLACE" if replace else "IGNORE"
                conn.execute(f"INSERT OR {verb} INTO workflow_records VALUES (?,?,?)", tuple(record.values()))

    def _record(self, identifier, kind, payload):
        record = {'id': identifier, 'kind': kind, 'payload': encoded(payload)}
        if self.mongo is not None:
            record.update({key: payload.get(key) for key in
                           ('instance', 'sort_at', 'member', 'batch_id', 'task_id', 'task_alias', 'stage',
                            'action', 'result', 'provenance', 'active', 'search_text', 'display_date')})
        return record

    def save_newer(self, identifier, kind, payload):
        """Publish a checkpoint only if its attempt began at least as recently.

        This comparison happens in storage, so an expired worker cannot replace
        a newer worker's checkpoint after its source request finally returns.
        ISO UTC ``sort_at`` values are required, as with workflow checkpoints.
        """
        record = self._record(identifier, kind, payload)
        stamp = payload['sort_at']
        if self.mongo is not None:
            from pymongo.errors import DuplicateKeyError
            try:
                result = self.mongo['workflow_records'].update_one(
                    {'_id': identifier, '$or': [{'sort_at': {'$lte': stamp}}, {'sort_at': {'$exists': False}}]},
                    {'$set': record}, upsert=True)
                return bool(result.modified_count or result.matched_count or result.upserted_id)
            except DuplicateKeyError:
                return False
        params = {**record, 'stamp': stamp}
        field = "workflow_records.payload::jsonb ->> 'sort_at'" if self.engine is not None else "json_extract(workflow_records.payload, '$.sort_at')"
        sql = ("INSERT INTO workflow_records (id,kind,payload) VALUES (:id,:kind,:payload) "
               "ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload WHERE " +
               f"{field} IS NULL OR {field} <= :stamp")
        if self.engine is not None:
            from sqlalchemy import text
            with self.engine.begin() as conn:
                return conn.execute(text(sql), params).rowcount == 1
        with self._sqlite() as conn:
            return conn.execute(sql, params).rowcount == 1

    def save_many(self, kind, payloads, *, replace=False):
        records = [self._record(payload['id'], kind, payload) for payload in payloads]
        if not records:
            return
        if self.mongo is not None:
            from pymongo import UpdateOne
            operator = '$set' if replace else '$setOnInsert'
            self.mongo['workflow_records'].bulk_write([
                UpdateOne({'_id': record['id']}, {operator: record}, upsert=True) for record in records], ordered=False)
        elif self.engine is not None:
            from sqlalchemy import text
            conflict = 'DO UPDATE SET payload=EXCLUDED.payload' if replace else 'DO NOTHING'
            with self.engine.begin() as conn:
                conn.execute(text('INSERT INTO workflow_records (id,kind,payload) VALUES (:id,:kind,:payload) '
                                  'ON CONFLICT (id) ' + conflict), records)
        else:
            verb = 'REPLACE' if replace else 'IGNORE'
            with self._sqlite() as conn:
                conn.executemany(f'INSERT OR {verb} INTO workflow_records VALUES (?,?,?)',
                                 [tuple(record.values()) for record in records])

    def unread_count(self, instance):
        if self.mongo is not None:
            collection = self.mongo['workflow_records']
            total = collection.count_documents({'kind': 'notification', 'instance': instance})
            read = collection.count_documents({'kind': 'notification-read', 'instance': instance})
            return max(0, total - read)
        field = "n.payload::jsonb ->> 'instance'" if self.engine is not None else "json_extract(n.payload, '$.instance')"
        sql = ("SELECT COUNT(*) FROM workflow_records n WHERE n.kind='notification' AND " + field + "=:instance "
               "AND NOT EXISTS (SELECT 1 FROM workflow_records r WHERE r.id='read:' || n.id)")
        if self.engine is not None:
            from sqlalchemy import text
            with self.engine.connect() as conn: return conn.execute(text(sql), {'instance': instance}).scalar_one()
        with self._sqlite() as conn: return conn.execute(sql, {'instance': instance}).fetchone()[0]

    def members(self, instance):
        if self.mongo is not None:
            return sorted(value for value in self.mongo['workflow_records'].distinct('member',
                {'kind': 'event', 'instance': instance}) if value)
        field = "payload::jsonb ->> 'member'" if self.engine is not None else "json_extract(payload, '$.member')"
        scope = "payload::jsonb ->> 'instance'" if self.engine is not None else "json_extract(payload, '$.instance')"
        sql = "SELECT DISTINCT " + field + " FROM workflow_records WHERE kind='event' AND " + scope + '=:instance'
        if self.engine is not None:
            from sqlalchemy import text
            with self.engine.connect() as conn: rows = conn.execute(text(sql), {'instance': instance}).fetchall()
        else:
            with self._sqlite() as conn: rows = conn.execute(sql, {'instance': instance}).fetchall()
        return sorted(row[0] for row in rows if row[0])

    def get(self, identifier):
        if self.mongo is not None:
            row = self.mongo['workflow_records'].find_one({'_id': identifier})
            return json.loads(row['payload']) if row else None
        if self.engine is not None:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                row = conn.execute(text('SELECT payload FROM workflow_records WHERE id=:id'), {'id': identifier}).first()
        else:
            with self._sqlite() as conn:
                row = conn.execute('SELECT payload FROM workflow_records WHERE id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, kind, instance, filters=None, page=1, page_size=25):
        """Filter/paginate in storage, so the browser never receives the ledger."""
        filters = filters or {}
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        exact = {key: filters[key] for key in ('member', 'batch_id', 'task_id', 'stage', 'action', 'provenance')
                 if filters.get(key)}
        if self.mongo is not None:
            query = {'kind': kind, 'instance': instance, 'active': {'$ne': False}, **exact}
            if 'task_id' in query:
                task = query.pop('task_id')
                query['$or'] = [{'task_id': task}, {'task_alias': task}]
            if filters.get('start') or filters.get('end'):
                query['display_date'] = {}
                if filters.get('start'): query['display_date']['$gte'] = filters['start']
                if filters.get('end'): query['display_date']['$lte'] = filters['end']
            if filters.get('search'):
                query['search_text'] = {'$regex': re.escape(filters['search'].casefold())}
            collection = self.mongo['workflow_records']
            total = collection.count_documents(query)
            rows = collection.find(query).sort([('sort_at', -1), ('_id', -1)]).skip((page - 1) * page_size).limit(page_size)
            return [json.loads(row['payload']) for row in rows], total
        postgres = self.engine is not None
        def field(key):
            return f"(payload::jsonb ->> '{key}')" if postgres else f"json_extract(payload, '$.{key}')"
        where = ['kind=:kind', field('instance') + '=:instance',
                 f"COALESCE(CAST({field('active')} AS TEXT), 'true') NOT IN ('false', '0')"]
        params = {'kind': kind, 'instance': instance, 'limit': page_size, 'offset': (page - 1) * page_size}
        for key, value in exact.items():
            where.append('(' + field('task_id') + '=:task_id OR ' + field('task_alias') + '=:task_id)'
                         if key == 'task_id' else field(key) + '=:' + key)
            params[key] = value
        for key, operator in (('start', '>='), ('end', '<=')):
            if filters.get(key):
                where.append(field('display_date') + operator + ':' + key)
                params[key] = filters[key]
        if filters.get('search'):
            # Literal substring search: '%' and '_' must not become wildcards.
            value = filters['search'].casefold().replace('!', '!!').replace('%', '!%').replace('_', '!_')
            where.append(field('search_text') + " LIKE :search ESCAPE '!' ")
            params['search'] = '%' + value + '%'
        predicate = ' AND '.join(where)
        count_sql = 'SELECT COUNT(*) FROM workflow_records WHERE ' + predicate
        rows_sql = ('SELECT payload FROM workflow_records WHERE ' + predicate +
                    ' ORDER BY ' + field('sort_at') + ' DESC, id DESC LIMIT :limit OFFSET :offset')
        if postgres:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                total = conn.execute(text(count_sql), params).scalar_one()
                rows = conn.execute(text(rows_sql), params).fetchall()
        else:
            with self._sqlite() as conn:
                total = conn.execute(count_sql, params).fetchone()[0]
                rows = conn.execute(rows_sql, params).fetchall()
        return [json.loads(row[0]) for row in rows], total

    def acquire_lease(self, instance, owner, seconds=300):
        from datetime import timedelta
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        identifier = f'lease:{instance}'
        payload = {'instance': instance, 'owner': owner, 'expires': expires}
        if self.mongo is not None:
            from pymongo.errors import DuplicateKeyError
            try:
                row = self.mongo['workflow_records'].find_one_and_update(
                    {'_id': identifier, '$or': [{'expires': {'$lte': now}}, {'expires': {'$exists': False}}]},
                    {'$set': {'kind': 'lease', 'payload': encoded(payload), 'expires': expires, 'owner': owner}}, upsert=True)
                return row is None or row.get('expires', '') <= now
            except DuplicateKeyError:
                return False
        params = {'id': identifier, 'payload': encoded(payload), 'now': now}
        expiry = "payload::jsonb ->> 'expires'" if self.engine is not None else "json_extract(payload, '$.expires')"
        sql = ("INSERT INTO workflow_records (id,kind,payload) VALUES (:id,'lease',:payload) "
               "ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload WHERE " + expiry + ' <= :now')
        if self.engine is not None:
            from sqlalchemy import text
            # Qualify the existing-row payload in PostgreSQL's conflict clause.
            sql = sql.replace("WHERE payload::", "WHERE workflow_records.payload::")
            with self.engine.begin() as conn:
                return conn.execute(text(sql), params).rowcount == 1
        with self._sqlite() as conn:
            return conn.execute(sql, params).rowcount == 1

    def release_lease(self, instance, owner):
        identifier = f'lease:{instance}'
        payload = {'instance': instance, 'owner': owner, 'expires': utc_now()}
        if self.mongo is not None:
            self.mongo['workflow_records'].update_one({'_id': identifier, 'owner': owner},
                {'$set': {'payload': encoded(payload), 'expires': payload['expires']}})
        else:
            owner_field = "payload::jsonb ->> 'owner'" if self.engine is not None else "json_extract(payload, '$.owner')"
            sql = 'UPDATE workflow_records SET payload=:payload WHERE id=:id AND ' + owner_field + '=:owner'
            params = {'payload': encoded(payload), 'id': identifier, 'owner': owner}
            if self.engine is not None:
                from sqlalchemy import text
                with self.engine.begin() as conn: conn.execute(text(sql), params)
            else:
                with self._sqlite() as conn: conn.execute(sql, params)

    def records(self, kind):
        if self.mongo is not None:
            return [json.loads(row["payload"]) for row in self.mongo["workflow_records"].find({"kind": kind}, {"payload": 1})]
        if self.engine is not None:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                rows = conn.execute(text("SELECT payload FROM workflow_records WHERE kind=:kind"), {"kind": kind})
                return [json.loads(row[0]) for row in rows]
        with self._sqlite() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM workflow_records WHERE kind=?", (kind,))]


def observation(source, row, instance, directory, canonical_name, phase=None):
    raw = {key: value for key, value in row.items() if key in FIELDS}
    source_id = raw.get("review_log_id") if source != "request" else raw.get("id")
    discriminator = str(source_id) if source_id is not None else hashlib.sha256(encoded(raw).encode()).hexdigest()
    key = f"{instance}:{source}:{discriminator}:{phase or 'action'}"
    batch = raw.get("assignment_batch_id")
    if not batch:
        tokens = set(re.findall(r"\b(?:assignment_batch_id|assigned_batch_id)\s*=\s*(ab_[A-Za-z0-9_-]+)", str(raw.get("decision_note") or "")))
        batch = next(iter(tokens)) if len(tokens) == 1 else None
    request = source == "request"
    actor_id = (raw.get("decided_by") if phase == "decision" else raw.get("returned_by")) if request else raw.get("reviewer_id")
    actor = directory.get(str(actor_id), {})
    actor_name = actor.get("username") or raw.get("reviewer_username") or raw.get("returned_by_name")
    fingerprint = hashlib.sha256(encoded({'raw': raw, 'actor': actor_name, 'role': actor.get('role')}).encode()).hexdigest()
    member_id = raw.get("requester_id") if request else raw.get("assignee_id", raw.get("slicer_id"))
    member_name = raw.get("requester_name") if request else raw.get("assignee_username", raw.get("slicer"))
    member_name = member_name or directory.get(str(member_id), {}).get("username")
    stamp_raw = (raw.get("decided_at") if phase == "decision" else raw.get("created_at")) if request else raw.get("reviewed_at")
    try:
        stamp = instant(stamp_raw)
    except (ValueError, TypeError):
        stamp = None
    reasons = ["Actor role/action semantics not yet verified"]
    if not batch:
        reasons.append("Batch identity not established")
    if not stamp:
        reasons.append("Source event time missing or invalid")
    if source_id is None:
        reasons.append("Immutable source action ID missing")
    return {
        "id": key + ":" + fingerprint, "event_key": key, "source": source,
        "source_id": source_id, "source_instance": instance, "observed_at": utc_now(),
        "batch_id": batch, "task_id": raw.get("task_id", raw.get("id") if not request and source == "task-review" else None),
        "task_alias": raw.get("task_desensitize_id"), "member_id": member_id,
        "member": canonical_name(member_id, member_name) or member_name or (f"Account {member_id}" if member_id is not None else "Unresolved"),
        "actor_id": actor_id, "actor": actor_name or (f"Account {actor_id}" if actor_id is not None else "Unresolved"),
        "actor_type": None, "actor_role_observed": actor.get("role"),
        "action": (str(raw.get("request_type") or "request") + ":" + str(phase)) if request else raw.get("review_action"),
        "result": raw.get("status") if request else raw.get("review_decision"),
        "previous_status": raw.get("review_previous_status"), "current_status": raw.get("review_current_status"),
        "reason": raw.get("review_comment") or raw.get("reason") or raw.get("decision_note") or "",
        "workflow": raw.get("workflow_type") or "slice",
        "event_at": stamp.astimezone(timezone.utc).isoformat() if stamp else None,
        "date": stamp.date().isoformat() if stamp else "", "time": stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "Unavailable",
        "verification": "Unresolved", "resolution_reason": "; ".join(reasons),
        "raw": raw, "normalizer_version": 2,
    }


class WorkflowHistory:
    PAGE_SIZE = 200
    PAGES_PER_SYNC = 3

    def __init__(self, manager, store=None):
        self.manager = manager
        self.store = store or WorkflowStore(manager.db)
        self.reader = DailyWorkSource(manager.scraper)
        self.instance = hashlib.sha256(manager.scraper._base_url.rstrip("/").encode()).hexdigest()[:16]
        self.last_sync = None
        self.operator_id = None

    def sync(self, force=False):
        from uuid import uuid4
        with LOCK:
            if not force and self.last_sync and (datetime.now(timezone.utc) - self.last_sync).total_seconds() < 300:
                return
            owner = uuid4().hex
            if not self.store.acquire_lease(self.instance, owner):
                return
            try:
                self._sync_sources()
                # Rebuild projection even after partial scans: durable source
                # evidence survives failures/crashes between ingestion/projection.
                self._project()
                self.last_sync = datetime.now(timezone.utc)
            finally:
                self.store.release_lease(self.instance, owner)

    def _sync_sources(self):
        try:
            me = self.reader.request('/api/auth/me')
            self.operator_id = me.get('id') or me.get('user_id') or me.get('user', {}).get('id')
        except Exception:
            self.operator_id = None
        checkpoint = {row["source"]: row for row in self.store.records("checkpoint") if row.get("instance") == self.instance}
        try:
            users = self.reader.request("/api/users")
            directory = {str(row["id"]): {key: row.get(key) for key in ("username", "role")} for row in users.get("items", users.get("data", []))}
        except Exception:
            directory = {}
        for source, path in ENDPOINTS.items():
            state = checkpoint.get(source, {"source": source, "instance": self.instance, "page": 1, "cursor": None, "complete": False})
            # Always revisit the newest page, while advancing the older
            # history cursor. Completed scans restart to revisit decisions.
            try:
                if state["page"] > 1 or state.get("cursor"):
                    self._page(source, path, {"page": 1}, directory)
                seen = set()
                seen_ids = set()
                for _ in range(self.PAGES_PER_SYNC):
                    params = {"cursor": state["cursor"]} if state.get("cursor") else {"page": state["page"]}
                    token = encoded(params)
                    if token in seen:
                        raise ValueError("Repeated workflow pagination cursor")
                    seen.add(token)
                    rows, meta = self._page(source, path, params, directory)
                    ids = {str(row.get('review_log_id') or row.get('id')) for row in rows}
                    if rows and ids <= seen_ids:
                        raise ValueError('Workflow page did not advance')
                    seen_ids.update(ids)
                    more = meta.get("has_more")
                    if more is None:
                        size = int(meta.get('page_size') or self.PAGE_SIZE)
                        more = state["page"] * size < int(meta["total"]) if meta.get("total") is not None else len(rows) == size
                    if more and not rows:
                        raise ValueError("Empty workflow page before end of history")
                    cursor = meta.get("next_cursor") if more else None
                    if more and cursor and (cursor == state.get("cursor") or encoded({'cursor': cursor}) in seen):
                        raise ValueError("Workflow cursor did not advance")
                    state.update(page=state["page"] + 1 if more else 1, cursor=cursor,
                                 complete=not more, error=None, updated_at=utc_now())
                    state['limited'] = bool(meta.get('total_capped'))
                    if not more:
                        state["last_complete_at"] = utc_now()
                    self.store.save(f"checkpoint:{self.instance}:{source}", "checkpoint", state, replace=True)
                    if not more:
                        break
            except Exception as error:
                # Do not store URLs, tokens or server exception bodies.
                state.update(error=type(error).__name__, updated_at=utc_now())
                self.store.save(f"checkpoint:{self.instance}:{source}", "checkpoint", state, replace=True)

    def _page(self, source, path, params, directory):
        parameters = {"workflow_type": "slice", "page_size": self.PAGE_SIZE, **params}
        if source == 'request' and self.operator_id is not None:
            parameters['operator_id'] = self.operator_id
        payload = self.reader.request(path, params=parameters)
        rows = payload.get("data", payload.get("items"))
        if not isinstance(rows, list):
            raise ValueError("Workflow API response is missing its record list")
        observations = []
        for row in rows:
            if row.get("workflow_type", "slice") != "slice":
                continue
            phases = ["created", "decision"] if source == "request" and row.get("decided_at") else ["created"] if source == "request" else [None]
            for phase in phases:
                event = observation(source, row, self.instance, directory, self.manager._get_reporting_name, phase)
                observations.append(event)
        self.store.save_many('observation', observations)
        return rows, payload.get("meta", {})

    def _project(self):
        from slicing_dashboard.processing.workflow_events import project_events, project_notices
        snapshot = self.snapshot()
        batches = getattr(self.manager, '_batches_master_cache', {})
        batches = list(batches.values()) if isinstance(batches, dict) else batches
        # Polls often return identical evidence. Avoid replacing every event
        # and notification (and blocking other callbacks) when nothing changed.
        projection_fingerprint = hashlib.sha256(encoded({'rows': sorted(snapshot['rows'], key=lambda row: row['id']),
            'batches': sorted(batches, key=lambda row: str(row.get('batch_id', '')))}).encode()).hexdigest()
        if projection_fingerprint == getattr(self, '_projection_fingerprint', None):
            return
        # Preserve versions of the latest-state batch ledger independently of
        # global snapshots, including the capture time used by synthetic rows.
        batch_records = []
        for batch in batches:
            fingerprint = hashlib.sha256(encoded(batch).encode()).hexdigest()
            batch_records.append({'id': f'batch:{self.instance}:{batch.get("batch_id")}:{fingerprint}',
                                  'instance': self.instance, 'observed_at': utc_now(), 'batch': batch})
        self.store.save_many('batch-observation', batch_records)
        previous = [row for row in self.store.records('event') if row.get('instance') == self.instance]
        events = project_events(snapshot['rows'], batches, previous, self.instance, utc_now())
        versions = []
        for event in [*previous, *events]:
            fingerprint = hashlib.sha256(encoded(event).encode()).hexdigest()
            versions.append({'id': f'projection:{event["id"]}:{fingerprint}', 'instance': self.instance,
                             'event_id': event['id'], 'projection': event})
        self.store.save_many('projection-history', versions)
        self.store.save_many('event', events, replace=True)
        previous_notices = {row['id']: row for row in self.store.records('notification')}
        notices = project_notices(snapshot['rows'], self.instance, events, previous_notices.values())
        for notice in notices:
            prior = previous_notices.get(notice['id'])
            if prior:
                notice['observed_at'] = prior['observed_at']
        self.store.save_many('notification', notices, replace=True)
        # Set only after all writes succeed: a failed write must be retried.
        self._projection_fingerprint = projection_fingerprint

    def capture_batches(self):
        """Project newly refreshed ledger state without another upstream scan."""
        from uuid import uuid4
        with LOCK:
            owner = uuid4().hex
            if not self.store.acquire_lease(self.instance, owner):
                return
            try:
                self._project()
            finally:
                self.store.release_lease(self.instance, owner)

    def notifications(self, limit=10):
        notices, total = self.store.query('notification', self.instance, page_size=limit)
        # A separate read-state record prevents source updates/reconciliation
        # from marking a previously read notice unread again.
        for row in notices:
            row['is_read'] = self.store.get('read:' + row['id']) is not None
        return {'rows': notices, 'total': total, 'unread': self.store.unread_count(self.instance),
                'storage': self.store.storage_label, 'scope': 'Shared team inbox'}

    def mark_read(self, identifier):
        notice = self.store.get(identifier)
        if not notice or notice.get('instance') != self.instance or notice.get('recipient_scope') != 'shared-team':
            raise ValueError('Unknown notification')
        self.store.save('read:' + identifier, 'notification-read',
                        {'notification_id': identifier, 'instance': self.instance, 'read_at': utc_now()})
        return notice

    def mark_all_read(self):
        # Separate read records survive subsequent source/projection updates.
        stamp = utc_now()
        notices = [row for row in self.store.records('notification')
                   if row.get('instance') == self.instance and row.get('recipient_scope') == 'shared-team']
        self.store.save_many('notification-read', [
            {'id': 'read:' + row['id'], 'notification_id': row['id'],
             'instance': self.instance, 'read_at': stamp} for row in notices])
        return self.notifications()

    def snapshot(self):
        versions = self.store.records("observation")
        latest = {}
        for row in sorted(versions, key=lambda item: item["observed_at"]):
            if row["source_instance"] == self.instance:
                latest[row["event_key"]] = row
        return {"rows": list(latest.values()), "versions": len(versions),
                "checkpoints": [row for row in self.store.records("checkpoint") if row.get("instance") == self.instance],
                "storage": self.store.storage_label}
