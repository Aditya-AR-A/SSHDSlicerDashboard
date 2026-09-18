"""
Database management module.
Handles pushing and pulling data from the Neon PostgreSQL database.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from slicing_dashboard.config import get_settings


class DatabaseManager:
    """Manages connections and ETL operations to the PostgreSQL database."""

    def __init__(self):
        self.settings = get_settings()
        self.engine: Optional[Engine] = None
        self._connected: Optional[bool] = None
        if self.settings.database_url:
            url = self.settings.database_url
            if url.startswith('postgres://'):
                url = url.replace('postgres://', 'postgresql://', 1)
            connect_args = {}
            if 'postgresql' in url:
                connect_args = {'connect_timeout': 3}
            try:
                self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
            except Exception:
                self.engine = None

    def is_connected(self) -> bool:
        """Check if the database engine is available and responsive."""
        if self.engine is None:
            return False
        if self._connected is not None:
            return self._connected
        try:
            with self.engine.connect() as conn:
                conn.execute(pd.io.sql.text("SELECT 1"))
            self._connected = True
        except Exception:
            self._connected = False
        return self._connected

    def upsert_slicing_master(self, df: pd.DataFrame) ->None:
        """Upsert the slicing master table."""
        if not self.is_connected():
            return
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

    def upsert_transitions_master(self, df: pd.DataFrame) ->None:
        """Upsert the transitions master table."""
        if not self.is_connected():
            return
        try:
            try:
                existing_df = pd.read_sql_table('transitions_master', self.
                    engine)
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
            
        from datetime import datetime
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        
        if now.hour < 12:
            return
            
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

    def load_slicing_master(self) ->pd.DataFrame:
        """Load slicing master from database."""
        if not self.is_connected():
            raise RuntimeError('Database not connected.')
        return pd.read_sql_table('slicing_master', self.engine)

    def load_transitions_master(self) ->pd.DataFrame:
        """Load transitions master from database."""
        if not self.is_connected():
            raise RuntimeError('Database not connected.')
        return pd.read_sql_table('transitions_master', self.engine)

    def save_dashboard_snapshot(self, snapshot_data: dict) -> bool:
        """Save JSON snapshot to PostgreSQL database table 'dashboard_snapshot'."""
        if not self.is_connected():
            return False
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
            print(f"Error saving snapshot to database: {e}")
            return False

    def load_dashboard_snapshot(self) -> dict | None:
        """Load JSON snapshot from PostgreSQL database table 'dashboard_snapshot'."""
        if not self.is_connected():
            return None
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
            return None
        except Exception as e:
            print(f"Error loading snapshot from database: {e}")
            return None

