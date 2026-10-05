"""Repair the transitional compressed snapshot key without replacing newer writers."""
import base64
import json
import zlib


def migrate_snapshot_key(database):
    if not database.is_connected() or database.mongo_db is None:
        return {'connected': False, 'migrated': False}
    collection = database.mongo_db['dashboard_snapshot']
    envelope = collection.find_one({'_id': 'latest', 'snapshot_encoding': 'zlib-base64-v1'})
    if envelope is None:
        return {'connected': True, 'migrated': False, 'reason': 'legacy key already compatible'}
    snapshot = json.loads(zlib.decompress(base64.b64decode(envelope['snapshot_payload'], validate=True)))
    if not isinstance(snapshot, dict):
        raise ValueError('Dashboard snapshot payload must be an object')
    versioned = dict(envelope, _id='latest-compressed-v1')
    # Keep an existing versioned snapshot: a live writer may already have
    # saved newer data after the transitional document was read.
    inserted = collection.update_one({'_id': 'latest-compressed-v1'}, {'$setOnInsert': versioned}, upsert=True)
    legacy = dict(snapshot, _id='latest', updated_at=envelope.get('updated_at'))
    restored = collection.replace_one(
        {'_id': 'latest', 'snapshot_encoding': 'zlib-base64-v1', 'updated_at': envelope.get('updated_at')},
        legacy, upsert=False,
    )
    return {'connected': True, 'migrated': restored.matched_count == 1,
            'versioned_created': inserted.upserted_id is not None,
            'legacy_restored': restored.matched_count == 1,
            'updated_at': envelope.get('updated_at')}


if __name__ == '__main__':
    import contextlib
    import io
    from slicing_dashboard.db import DatabaseManager
    database = None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            database = DatabaseManager()
            outcome = migrate_snapshot_key(database)
    except Exception as error:
        outcome = {'migrated': False, 'error_class': type(error).__name__}
    print(json.dumps(outcome))
    if database is not None and database.mongo_client is not None:
        database.mongo_client.close()
