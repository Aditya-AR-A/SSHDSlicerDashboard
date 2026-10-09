"""Add derived chart fields without replacing source payloads or task evidence."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from pymongo import UpdateOne
from slicing_dashboard.config import get_settings
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.reporting.chart_projections import workflow_chart, daily_chart_rows


def backfill(start, end, apply=False):
    settings, database = get_settings(), DatabaseManager()
    if database.mongo_db is None:
        raise ConnectionError('Configured MongoDB unavailable')
    source = settings.dashboard_url.rstrip('/')
    instance = sha256(source.encode()).hexdigest()[:16]
    efficiency = 'efficiency:' + sha256(f'{source}:{settings.efficiency_leader_id}:2'.encode()).hexdigest()[:24]
    workflows = database.mongo_db['workflow_records']
    if apply:
        workflows.create_index([('kind', 1), ('instance', 1), ('action', 1),
                                ('active', 1), ('stage', 1), ('display_date', 1)],
                               name='chart_approval_lookup')
    scopes = [
        {'kind': 'event', 'instance': instance, 'active': True, 'action': 'Approved',
         'stage': {'$in': ['Leader', 'Auditor', 'Admin']}, 'provenance': {'$ne': 'Synthetic'}},
        {'kind': 'efficiency-capture', 'instance': efficiency}]
    result = {'workflow_records': 0, 'workflow_verified': 0, 'daily_records': 0, 'applied': apply}
    for scope in scopes:
        query = {**scope, 'display_date': {'$gte': start, '$lte': end}, 'chart_version': {'$ne': 1}}
        # Commit small groups before fetching more. A timeout can be resumed
        # without redownloading already projected audit records.
        batch = []
        for row in workflows.find(query, {'kind': 1, 'payload': 1}).batch_size(5):
            batch.append(row)
            if len(batch) == 5:
                result['workflow_verified'] += project_batch(workflows, batch, apply)
                result['workflow_records'] += len(batch)
                batch = []
        if batch:
            result['workflow_verified'] += project_batch(workflows, batch, apply)
            result['workflow_records'] += len(batch)
        print(json.dumps({'kind': scope['kind'], 'projected': result['workflow_records']}), flush=True)
    daily = database.mongo_db['daily_work_reports']
    for record in daily.find({'_id': {'$gte': start, '$lte': end}}).batch_size(1):
        chart = daily_chart_rows(record)
        if chart is None:
            continue
        result['daily_records'] += 1
        if apply:
            # Only the two new fields are written; a newer revision wins.
            revision = record.get('_revision', {'$exists': False})
            update = daily.update_one({'_id': record['_id'], '_revision': revision},
                                     {'$set': {'chart_rows': chart, 'chart_version': 1}})
            if update.matched_count:
                saved = daily.find_one({'_id': record['_id']}, {'chart_rows': 1, '_revision': 1})
                if saved.get('_revision', {'$exists': False}) == revision:
                    assert saved['chart_rows'] == chart
    database.mongo_client.close()
    return result


def project_batch(collection, rows, apply):
    expected, operations = {}, []
    for row in rows:
        chart = workflow_chart(row['kind'], json.loads(row['payload']))
        expected[row['_id']] = (chart, row['payload'])
        operations.append(UpdateOne({'_id': row['_id'], 'payload': row['payload']},
                                    {'$set': {'chart': chart, 'chart_version': 1}}))
    if not apply:
        return 0
    collection.bulk_write(operations, ordered=False)
    verified = 0
    # The compare-and-set already checks the exact original payload; projecting
    # only chart fields avoids downloading the same audit bodies a second time.
    unchanged = [{'_id': identity, 'payload': payload} for identity, (_, payload) in expected.items()]
    for row in collection.find({'$or': unchanged}, {'chart': 1, 'chart_version': 1}).batch_size(5):
        chart, payload = expected[row['_id']]
        assert row.get('chart') == chart and row.get('chart_version') == 1
        verified += 1
    return verified


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2026-09-01')
    parser.add_argument('--end', default=today_iso())
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = backfill(args.start, args.end, args.apply)
    target = Path('data/reports/performance-audit/chart-field-backfill.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
