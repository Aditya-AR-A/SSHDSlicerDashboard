"""
Database management module.
Handles pushing and pulling data from MongoDB Atlas (primary) or PostgreSQL (fallback).
"""
from __future__ import annotations
from typing import Optional, Any
from datetime import datetime
import pandas as pd
from slicing_dashboard.config import get_settings


class DatabaseManager:
    """Manages connections and ETL operations to MongoDB Atlas and PostgreSQL."""

    def __init__(self):
        self.settings = get_settings()
        self.engine = None
        self._connected: Optional[bool] = None

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
        if self._connected is not None:
            return self._connected

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
        if not self.is_connected():
            return False

        # ── MongoDB Storage (Preferred) ──────────────────────────────
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['dashboard_snapshot']
                doc = dict(snapshot_data)
                doc['_id'] = 'latest'
                doc['updated_at'] = datetime.now().isoformat()
                coll.replace_one({'_id': 'latest'}, doc, upsert=True)

                # Also populate granular batches_master collection for fast indexing
                bm_recs = snapshot_data.get("batches_master_records", [])
                if isinstance(bm_recs, dict):
                    if "batches" in bm_recs and isinstance(bm_recs["batches"], dict):
                        bm_recs = list(bm_recs["batches"].values())
                    else:
                        bm_recs = [v for v in bm_recs.values() if isinstance(v, dict)]
                if isinstance(bm_recs, list):
                    bm_coll = self.mongo_db['batches_master']
                    for b in bm_recs:
                        if isinstance(b, dict):
                            bid = b.get("batch_id")
                            if bid:
                                b_copy = dict(b)
                                b_copy['_id'] = bid
                                bm_coll.replace_one({'_id': bid}, b_copy, upsert=True)

                # Also populate batch_returns collection
                br_records = snapshot_data.get("batch_returns_records", [])
                if isinstance(br_records, dict):
                    br_records = br_records.get("records", [])
                if isinstance(br_records, list):
                    br_coll = self.mongo_db['batch_returns']
                    for r in br_records:
                        if isinstance(r, dict):
                            rid = r.get("id") or r.get("task_id") or r.get("batch_id")
                            if rid:
                                r_copy = dict(r)
                                r_copy['_id'] = str(rid)
                                br_coll.replace_one({'_id': str(rid)}, r_copy, upsert=True)

                return True
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"Error saving snapshot to MongoDB: {e}")

        # ── PostgreSQL Storage (Fallback) ────────────────────────────
        if self.engine is not None:
            try:
                import json
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
                return True
            except Exception as e:
                print(f"Error saving snapshot to PostgreSQL: {e}")

        return False

    def load_dashboard_snapshot(self) -> dict | None:
        """Load JSON snapshot from MongoDB Atlas or PostgreSQL table 'dashboard_snapshot'."""
        if not self.is_connected():
            return None

        # ── MongoDB Load (Preferred) ─────────────────────────────────
        if self.mongo_db is not None:
            try:
                coll = self.mongo_db['dashboard_snapshot']
                doc = coll.find_one({'_id': 'latest'})
                if doc and isinstance(doc, dict):
                    doc.pop('_id', None)
                    return doc
            except Exception as e:
                print(f"Error loading snapshot from MongoDB: {e}")

        # ── PostgreSQL Load (Fallback) ───────────────────────────────
        if self.engine is not None:
            try:
                import json
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
