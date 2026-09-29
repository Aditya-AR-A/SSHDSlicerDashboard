"""
Upload dashboard snapshot and master records to PostgreSQL database.
Allows Vercel serverless deployments to load verified data directly from DB without local file dependencies.
"""
import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from slicing_dashboard.config import get_settings, DATA_DIR
from slicing_dashboard.db import DatabaseManager

def upload_json_to_database(custom_url: str | None = None) -> bool:
    settings = get_settings()
    db_url = custom_url or settings.database_url
    if not db_url:
        print("❌ Error: No DATABASE_URL found in environment or .env file.")
        return False

    print(f"Connecting to database...")
    db = DatabaseManager()
    if custom_url:
        from sqlalchemy import create_engine
        connect_args = {}
        if 'postgresql' in custom_url:
            connect_args = {'connect_timeout': 5}
        db.engine = create_engine(custom_url, connect_args=connect_args, pool_pre_ping=True)
        db._connected = None

    if not db.is_connected():
        print("[ERROR] Could not connect to database with current credentials.")
        return False

    print("[OK] Database connected successfully!")

    # 1. Load snapshot_cache.json
    snapshot_file = DATA_DIR / "snapshot_cache.json"
    if not snapshot_file.exists():
        print(f"❌ Error: Snapshot file {snapshot_file} not found.")
        return False

    with open(snapshot_file, "r", encoding="utf-8") as f:
        snapshot_payload = json.load(f)

    # 2. Ensure batches_master and batch_returns are included in payload
    batches_master_file = DATA_DIR / "batches_master.json"
    if batches_master_file.exists():
        with open(batches_master_file, "r", encoding="utf-8") as f:
            bm_data = json.load(f)
            if isinstance(bm_data, dict):
                if "batches" in bm_data:
                    b_raw = bm_data["batches"]
                    if isinstance(b_raw, list):
                        snapshot_payload["batches_master_records"] = [v for v in b_raw if isinstance(v, dict)]
                    elif isinstance(b_raw, dict):
                        snapshot_payload["batches_master_records"] = list(b_raw.values())
                else:
                    snapshot_payload["batches_master_records"] = [v for v in bm_data.values() if isinstance(v, dict)]
            elif isinstance(bm_data, list):
                snapshot_payload["batches_master_records"] = [v for v in bm_data if isinstance(v, dict)]

    batch_returns_file = DATA_DIR / "batch_returns_history.json"
    if batch_returns_file.exists():
        with open(batch_returns_file, "r", encoding="utf-8") as f:
            br_raw = json.load(f)
            if isinstance(br_raw, dict) and "records" in br_raw:
                snapshot_payload["batch_returns_records"] = br_raw["records"]
            else:
                snapshot_payload["batch_returns_records"] = br_raw

    # 3. Save to database table 'dashboard_snapshot'
    print("Uploading snapshot payload to 'dashboard_snapshot' table...")
    success = db.save_dashboard_snapshot(snapshot_payload)
    if not success:
        print("[ERROR] Failed to save snapshot to database.")
        return False

    # 4. Verify upload by reading back
    print("Verifying uploaded data from database...")
    loaded = db.load_dashboard_snapshot()
    if not loaded:
        print("[ERROR] Verification failed: loaded snapshot is empty.")
        return False

    total_bm = len(loaded.get("batches_master_records", []))
    total_br = len(loaded.get("batch_returns_records", []))
    sync_time = loaded.get("last_sync_time")
    print(f"[OK] Success! Snapshot uploaded and verified in database:")
    print(f"   - Last sync time: {sync_time}")
    print(f"   - Batches master records: {total_bm}")
    print(f"   - Batch returns records: {total_br}")
    print(f"   - Cache keys: {len(loaded.get('cache', {}))}")
    return True

if __name__ == "__main__":
    url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    res = upload_json_to_database(url_arg)
    sys.exit(0 if res else 1)
