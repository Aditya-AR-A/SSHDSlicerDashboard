"""
Database management module.
Handles pushing and pulling data from MongoDB Atlas (primary) or PostgreSQL (fallback).
"""
from __future__ import annotations
from typing import Optional, Any
from datetime import datetime
import hashlib
import json
import base64
import zlib
from time import perf_counter
import pandas as pd
from slicing_dashboard.config import get_settings
from slicing_dashboard.processing.source_policy import SOURCE_TTL_SECONDS


class DatabaseManager:
    """Manages connections and ETL operations to MongoDB Atlas and PostgreSQL."""

    def __init__(self):
        self.settings = get_settings()
        self.engine = None
        self._connected: Optional[bool] = None
        self._snapshot_document_fingerprints = {}
        self.last_snapshot_profile = {}

        # ── 1. MongoDB Atlas Initialization ─────────────────────────
        self.mongo_client = None
        self.mongo_db = None
        mongo_url = getattr(self.settings, 'mongo_uri', None) or (
            self.settings.database_url if self.settings.database_url and (
                self.settings.database_url.startswith('mongodb://') or 
                self.settings.database_url.startswith('mongodb+srv://')
            ) else None
        )
        if mongo_url:
            try:
                import pymongo
                self.mongo_client = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=4000)
                # Default database name 'slicing_dashboard'
                self.mongo_db = self.mongo_client['slicing_dashboard']
            except Exception as e:
                print(f"Warning: Failed to initialize MongoDB: {e}")

        # ── 2. PostgreSQL Initialization (Fallback) ──────────────────
        if not self.mongo_client and self.settings.database_url and (
            'postgres' in self.settings.database_url or 'postgresql' in self.settings.database_url
        ):
            url = self.settings.database_url
            if url.startswith('postgres://'):
                url = url.replace('postgres://', 'postgresql://', 1)
            connect_args = {'connect_timeout': 3}
            try:
                from sqlalchemy import create_engine
                self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
            except Exception:
                self.engine = None

    def is_connected(self) -> bool:
        """Check if MongoDB or PostgreSQL database is available and responsive."""
        if self._connected is True:
            return True
        if (self._connected is False and
                perf_counter() - getattr(self, '_last_connection_check', -float('inf')) < SOURCE_TTL_SECONDS):
            return False
        self._last_connection_check = perf_counter()

        # Check MongoDB first
        if self.mongo_client is not None and self.mongo_db is not None:
            try:
                self.mongo_client.admin.command('ping')
                self._connected = True
                return True
            except Exception:
                pass

        # Check PostgreSQL
        if self.engine is not None:
            try:
                with self.engine.connect() as conn:
                    conn.execute(pd.io.sql.text("SELECT 1"))
                self._connected = True
                return True
            except Exception:
                pass

        self._connected = False
        return False

    def upsert_slicing_master(self, df: pd.DataFrame) -> None:
        """Upsert the slicing master records."""
        if not self.is_connected():
            return
        if df.empty:
            return

        # MongoDB upsert
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['slicing_master']
                records = df.to_dict('records')
                for r in records:
                    doc_id = r.get('id') or r.get('task_id')
                    if doc_id:
                        r_copy = dict(r)
                        r_copy['_id'] = doc_id
                        coll.replace_one({'_id': doc_id}, r_copy, upsert=True)
                return
            except Exception as e:
                print(f"Error in MongoDB upsert_slicing_master: {e}")

        # PostgreSQL fallback
        if self.engine is not None:
            try:
                try:
                    existing_df = pd.read_sql_table('slicing_master', self.engine)
                    combined_df = pd.concat([existing_df, df]).drop_duplicates(
                        subset=['id'], keep='last')
                except ValueError:
                    combined_df = df
                combined_df.to_sql('slicing_master', self.engine, if_exists=
                    'replace', index=False, chunksize=1000, method='multi')
            except Exception as e:
                print(f"Error in upsert_slicing_master: {e}")

    def upsert_transitions_master(self, df: pd.DataFrame) -> None:
        """Upsert the transitions master records."""
        if not self.is_connected():
            return
        if df.empty:
            return

        # MongoDB upsert
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['transitions_master']
                records = df.to_dict('records')
                for r in records:
                    t_id = r.get('task_id')
                    t_type = r.get('type')
                    t_date = r.get('date')
                    key = f"{t_id}_{t_type}_{t_date}"
                    r_copy = dict(r)
                    r_copy['_id'] = key
                    coll.replace_one({'_id': key}, r_copy, upsert=True)
                return
            except Exception as e:
                print(f"Error in MongoDB upsert_transitions_master: {e}")

        # PostgreSQL fallback
        if self.engine is not None:
            try:
                try:
                    existing_df = pd.read_sql_table('transitions_master', self.engine)
                    combined_df = pd.concat([existing_df, df]).drop_duplicates(
                        subset=['task_id', 'type', 'date'], keep='last')
                except ValueError:
                    combined_df = df
                combined_df.to_sql('transitions_master', self.engine, if_exists
                    ='replace', index=False, chunksize=1000, method='multi')
            except Exception as e:
                print(f"Error in upsert_transitions_master: {e}")

    def upsert_slicing_history_snapshot(self, df: pd.DataFrame) -> None:
        """Create a daily snapshot of the slicing master at 12 PM or earliest after."""
        if not self.is_connected():
            return
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        if now.hour < 12:
            return

        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['slicing_history']
                if coll.find_one({'snapshot_date': today_str}):
                    return
                snapshot_df = df.copy()
                snapshot_df['snapshot_date'] = today_str
                records = snapshot_df.to_dict('records')
                if records:
                    coll.insert_many(records)
                return
            except Exception as e:
                print(f"Error in MongoDB upsert_slicing_history_snapshot: {e}")

        if self.engine is not None:
            try:
                try:
                    query = f"SELECT 1 FROM slicing_history WHERE snapshot_date = '{today_str}' LIMIT 1"
                    result = pd.read_sql_query(query, self.engine)
                    if not result.empty:
                        return
                except Exception:
                    pass

                snapshot_df = df.copy()
                snapshot_df['snapshot_date'] = today_str
                snapshot_df.to_sql('slicing_history', self.engine, if_exists='append', index=False, chunksize=1000, method='multi')
            except Exception as e:
                print(f"Error in upsert_slicing_history_snapshot: {e}")

    def load_slicing_master(self) -> pd.DataFrame:
        """Load slicing master from database."""
        if not self.is_connected():
            raise RuntimeError('Database not connected.')
        if self.mongo_db is not None:
            docs = list(self.mongo_db['slicing_master'].find({}, {'_id': 0}))
            return pd.DataFrame(docs)
        if self.engine is not None:
            return pd.read_sql_table('slicing_master', self.engine)
        return pd.DataFrame()

    def load_transitions_master(self) -> pd.DataFrame:
        """Load transitions master from database."""
        if not self.is_connected():
            raise RuntimeError('Database not connected.')
        if self.mongo_db is not None:
            docs = list(self.mongo_db['transitions_master'].find({}, {'_id': 0}))
            return pd.DataFrame(docs)
        if self.engine is not None:
            return pd.read_sql_table('transitions_master', self.engine)
        return pd.DataFrame()

    def save_dashboard_snapshot(self, snapshot_data: dict) -> bool:
        """Save JSON snapshot to MongoDB Atlas or PostgreSQL table 'dashboard_snapshot'."""
        self.last_snapshot_profile = {'saved': False}
        if not self.is_connected():
            self.last_snapshot_profile['backend'] = 'unavailable'
            self.last_snapshot_profile['error_class'] = 'DatabaseDisconnected'
            return False

        # ── MongoDB Storage (Preferred) ──────────────────────────────
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['dashboard_snapshot']
                started = perf_counter()
                raw_snapshot = json.dumps(snapshot_data, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
                compressed_snapshot = zlib.compress(raw_snapshot)
                doc = {'_id': 'latest-compressed-v1', 'snapshot_encoding': 'zlib-base64-v1',
                       'snapshot_payload': base64.b64encode(compressed_snapshot).decode('ascii'),
                       'updated_at': datetime.now().isoformat()}
                self.last_snapshot_profile = {'saved': False, 'backend': 'mongodb',
                                              'json_bytes': len(raw_snapshot), 'compressed_bytes': len(compressed_snapshot),
                                              'compression_seconds': round(perf_counter() - started, 4)}
                started = perf_counter()
                coll.replace_one({'_id': 'latest-compressed-v1'}, doc, upsert=True)
                self.last_snapshot_profile['snapshot_write_seconds'] = round(perf_counter() - started, 4)

                # Also populate granular batches_master collection for fast indexing
                bm_recs = snapshot_data.get("batches_master_records", [])
                if isinstance(bm_recs, dict):
                    if "batches" in bm_recs and isinstance(bm_recs["batches"], dict):
                        bm_recs = list(bm_recs["batches"].values())
                    else:
                        bm_recs = [v for v in bm_recs.values() if isinstance(v, dict)]
                if isinstance(bm_recs, list):
                    started = perf_counter()
                    self._upsert_snapshot_records('batches_master', bm_recs, ('batch_id',))
                    self.last_snapshot_profile['batches_write_seconds'] = round(perf_counter() - started, 4)

                # Also populate batch_returns collection
                br_records = snapshot_data.get("batch_returns_records", [])
                if isinstance(br_records, dict):
                    br_records = br_records.get("records", [])
                if isinstance(br_records, list):
                    started = perf_counter()
                    self._upsert_snapshot_records('batch_returns', br_records, ('event_id', 'id', 'task_id', 'batch_id'))
                    self.last_snapshot_profile['returns_write_seconds'] = round(perf_counter() - started, 4)

                self.last_snapshot_profile['saved'] = True
                return True
            except Exception as e:
                self.last_snapshot_profile['error_class'] = type(e).__name__
                import traceback
                traceback.print_exc()
                print(f"Error saving snapshot to MongoDB: {e}")

        # ── PostgreSQL Storage (Fallback) ────────────────────────────
        if self.engine is not None:
            try:
                self.last_snapshot_profile['backend'] = 'postgresql'
                from sqlalchemy import text
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "CREATE TABLE IF NOT EXISTS dashboard_snapshot ("
                            "  key VARCHAR(50) PRIMARY KEY,"
                            "  data JSONB,"
                            "  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                            ")"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO dashboard_snapshot (key, data, updated_at) "
                            "VALUES ('latest', :data, NOW()) "
                            "ON CONFLICT (key) DO UPDATE SET "
                            "  data = EXCLUDED.data, "
                            "  updated_at = NOW()"
                        ),
                        {"data": json.dumps(snapshot_data)},
                    )
                self.last_snapshot_profile['saved'] = True
                return True
            except Exception as e:
                self.last_snapshot_profile['error_class'] = type(e).__name__
                print(f"Error saving snapshot to PostgreSQL: {e}")

        return False

    def _upsert_snapshot_records(self, collection_name: str, records: list[dict], id_fields: tuple[str, ...]) -> None:
        """Batch only changed ledger documents instead of sending one request per row."""
        from pymongo import ReplaceOne
        fingerprints = getattr(self, '_snapshot_document_fingerprints', {})
        self._snapshot_document_fingerprints = fingerprints
        changed = []
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = next((record.get(field) for field in id_fields if record.get(field)), None)
            if record_id is None:
                continue
            record_id = str(record_id)
            meaningful_fields = {name: value for name, value in record.items() if name != 'last_updated'}
            fingerprint = hashlib.sha256(json.dumps(meaningful_fields, sort_keys=True, default=str).encode('utf-8')).digest()
            key = (collection_name, record_id)
            if fingerprints.get(key) == fingerprint:
                continue
            document = dict(record, _id=record_id)
            changed.append((ReplaceOne({'_id': record_id}, document, upsert=True), key, fingerprint))
        collection = self.mongo_db[collection_name]
        for offset in range(0, len(changed), 500):
            chunk = changed[offset:offset + 500]
            collection.bulk_write([operation for operation, _, _ in chunk], ordered=False)
            for _, key, fingerprint in chunk:
                fingerprints[key] = fingerprint

    def load_dashboard_snapshot(self) -> dict | None:
        """Load JSON snapshot from MongoDB Atlas or PostgreSQL table 'dashboard_snapshot'."""
        if not self.is_connected():
            return None

        # ── MongoDB Load (Preferred) ─────────────────────────────────
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['dashboard_snapshot']
                doc = coll.find_one({'_id': 'latest-compressed-v1'})
                if doc is None:
                    doc = coll.find_one({'_id': 'latest'})
                if doc and isinstance(doc, dict):
                    if doc.get('snapshot_encoding') == 'zlib-base64-v1':
                        raw_snapshot = zlib.decompress(base64.b64decode(doc['snapshot_payload'], validate=True))
                        snapshot = json.loads(raw_snapshot.decode('utf-8'))
                        if not isinstance(snapshot, dict):
                            raise ValueError('Dashboard snapshot payload must be an object')
                        return snapshot
                    doc.pop('_id', None)
                    return doc
            except Exception as e:
                print(f"Error loading snapshot from MongoDB: {e}")

        # ── PostgreSQL Load (Fallback) ───────────────────────────────
        if self.engine is not None:
            try:
                from sqlalchemy import text
                with self.engine.connect() as conn:
                    res = conn.execute(
                        text("SELECT data FROM dashboard_snapshot WHERE key = 'latest' LIMIT 1")
                    ).fetchone()
                    if res and res[0]:
                        val = res[0]
                        return json.loads(val) if isinstance(val, str) else val
            except Exception as e:
                print(f"Error loading snapshot from PostgreSQL: {e}")

        return None

    def save_user_mappings(self, mappings_list: list[dict]) -> bool:
        """Save user mappings to MongoDB collection 'user_mappings'."""
        if not self.is_connected() or self.mongo_db is None:
            return False
        try:
            coll = self.mongo_db['user_mappings']
            valid_ids = [m['id'] for m in mappings_list if m.get('id')]
            # Remove any docs no longer in mappings_list if table was edited/rows deleted
            if valid_ids:
                coll.delete_many({'_id': {'$nin': valid_ids}})
            for m in mappings_list:
                doc = dict(m)
                doc['_id'] = doc['id']
                coll.replace_one({'_id': doc['_id']}, doc, upsert=True)
            return True
        except Exception as e:
            print(f"Error saving user mappings to MongoDB: {e}")
            return False

    def load_user_mappings(self, *, strict=False) -> list[dict]:
        """Load user mappings from MongoDB."""
        if not self.is_connected() or self.mongo_db is None:
            if strict:
                raise ConnectionError('Mapping storage unavailable')
            return []
        try:
            coll = self.mongo_db['user_mappings']
            docs = list(coll.find())
            return docs
        except Exception as e:
            if strict:
                raise ConnectionError('Mapping read failed') from None
            print(f"Error loading user mappings from MongoDB: {e}")
            return []

    def save_settlement_periods(self, periods: list[dict]) -> bool:
        """Save settlement periods to MongoDB collection 'settlement_periods'."""
        if not self.is_connected() or self.mongo_db is None:
            return False
        try:
            coll = self.mongo_db['settlement_periods']
            valid_ids = [p.get('_id', f"{p.get('start_date')}_{p.get('end_date')}") for p in periods if p.get('start_date')]
            if valid_ids:
                coll.delete_many({'_id': {'$nin': valid_ids}})
            for p in periods:
                doc = dict(p)
                doc['_id'] = doc.get('_id', f"{doc['start_date']}_{doc['end_date']}")
                coll.replace_one({'_id': doc['_id']}, doc, upsert=True)
            return True
        except Exception as e:
            print(f"Error saving settlement periods to MongoDB: {e}")
            return False

    def load_settlement_periods(self, *, strict=False) -> list[dict]:
        """Load settlement periods from MongoDB."""
        if not self.is_connected() or self.mongo_db is None:
            if strict:
                raise ConnectionError('Period storage unavailable')
            return []
        try:
            coll = self.mongo_db['settlement_periods']
            docs = list(coll.find())
            return docs
        except Exception as e:
            if strict:
                raise ConnectionError('Period read failed') from None
            print(f"Error loading settlement periods from MongoDB: {e}")
            return []

    def load_daily_work_reports(self, start_date: str, end_date: str) -> list[dict]:
        """Read dated submission observations, never current task-state snapshots."""
        if not self.is_connected():
            return []
        if self.mongo_db is not None:
            try:
                records = self.mongo_db['daily_work_reports'].find(
                    {'_id': {'$gte': start_date, '$lte': end_date}})
                return [{key: value for key, value in record.items() if key not in ('_id', '_revision')}
                        for record in records]
            except Exception as error:
                print(f"Could not read daily work reports from MongoDB: {error}")
        if self.engine is not None:
            try:
                from sqlalchemy import text
                with self.engine.begin() as connection:
                    self._ensure_daily_work_table(connection)
                    records = connection.execute(text(
                        'SELECT payload FROM daily_work_reports WHERE date >= :start AND date <= :end'),
                        {'start': start_date, 'end': end_date})
                    return [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in records]
            except Exception as error:
                print(f"Could not read daily work reports from PostgreSQL: {error}")
        return []

    def daily_work_start_date(self, end_date: str):
        """Earliest retained daily record, including history outside the chart range."""
        if not self.is_connected():
            return None
        if self.mongo_db is not None:
            try:
                record = self.mongo_db['daily_work_reports'].find_one(
                    {'_id': {'$lte': end_date}}, {'date': 1}, sort=[('_id', 1)])
                return record.get('date', record.get('_id')) if record else None
            except Exception as error:
                print(f'Could not read daily history start: {error}')
        if self.engine is not None:
            try:
                from sqlalchemy import text
                with self.engine.begin() as connection:
                    self._ensure_daily_work_table(connection)
                    return connection.execute(text(
                        'SELECT MIN(date) FROM daily_work_reports WHERE date <= :end'),
                        {'end': end_date}).scalar()
            except Exception as error:
                print(f'Could not read daily history start: {error}')
        return None

    @staticmethod
    def _ensure_daily_work_table(connection):
        from sqlalchemy import text
        connection.execute(text('CREATE TABLE IF NOT EXISTS daily_work_reports '
                                '(date TEXT PRIMARY KEY, payload JSONB NOT NULL)'))

    def save_daily_work_report(self, report: dict) -> bool:
        """Idempotently union task/day observations with concurrent-writer protection."""
        from slicing_dashboard.reporting.dashboard_reports import merge_daily_reports
        from slicing_dashboard.management.periods import today_iso

        if report.get('metadata', {}).get('available') is False:
            return False
        if not self.is_connected():
            return False
        report = {**report, 'metadata': {**report.get('metadata', {}),
                  'observed_on': max(report.get('metadata', {}).get('observed_on', report['date']), today_iso())}}
        day = report['date']
        if self.mongo_db is not None:
            try:
                from pymongo.errors import DuplicateKeyError
                collection = self.mongo_db['daily_work_reports']
                for _ in range(5):
                    existing = collection.find_one({'_id': day})
                    merged = merge_daily_reports(existing, report)
                    revision = (existing or {}).get('_revision', 0)
                    document = {**merged, '_id': day, '_revision': revision + 1}
                    if existing is None:
                        try:
                            collection.insert_one(document)
                            return True
                        except DuplicateKeyError:
                            continue
                    # An old writer cannot erase evidence captured by a newer refresh.
                    expected = revision if '_revision' in existing else {'$exists': False}
                    result = collection.replace_one({'_id': day, '_revision': expected}, document)
                    if result.matched_count:
                        return True
            except Exception as error:
                print(f"Could not save daily work report to MongoDB: {error}")
        if self.engine is not None:
            try:
                from sqlalchemy import text
                with self.engine.begin() as connection:
                    self._ensure_daily_work_table(connection)
                    # Lock the date even before its first row exists, so concurrent
                    # initial captures merge instead of replacing one another.
                    connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                                       {'key': f'daily_work_reports:{day}'})
                    existing = connection.execute(text(
                        'SELECT payload FROM daily_work_reports WHERE date = :date FOR UPDATE'),
                        {'date': day}).scalar()
                    if isinstance(existing, str):
                        existing = json.loads(existing)
                    merged = merge_daily_reports(existing, report)
                    connection.execute(text('INSERT INTO daily_work_reports (date, payload) '
                                            'VALUES (:date, CAST(:payload AS JSONB)) '
                                            'ON CONFLICT (date) DO UPDATE SET payload = EXCLUDED.payload'),
                                       {'date': day, 'payload': json.dumps(merged, allow_nan=False)})
                return True
            except Exception as error:
                print(f"Could not save daily work report to PostgreSQL: {error}")
        return False
