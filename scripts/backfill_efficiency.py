"""Backfill source-date completion/review status without inventing task events."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace

from slicing_dashboard.config import get_settings
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.scraper.http_scraper import HTTPScraper
from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.reporting.efficiency_history import EfficiencyHistory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default=(date.fromisoformat(today_iso()) - timedelta(days=29)).isoformat())
    parser.add_argument('--end', default=today_iso())
    parser.add_argument('--force', action='store_true', help='Refresh already retained historical status')
    args = parser.parse_args()
    settings = get_settings()
    manager = SimpleNamespace(settings=settings, db=DatabaseManager(), scraper=HTTPScraper(settings))
    cookie = os.environ.get('PIPELINE_AUTH')
    if cookie:
        manager.scraper._client.cookies.set('pipeline_auth', cookie)
        manager.scraper._authenticated = True
    for _ in range(3):
        manager.db._connected = None
        if manager.db.is_connected():
            break
    else:
        raise ConnectionError('Backfill requires the configured shared database')
    history = EfficiencyHistory(manager)
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if first > last or last > date.fromisoformat(today_iso()):
        raise ValueError('Invalid backfill range')
    rows, failed = [], []
    for offset in range((last - first).days + 1):
        day = (first + timedelta(days=offset)).isoformat()
        try:
            record = history.capture(day, force=args.force)
            stored = history.store.get(history.key(day))
            if not stored or stored['captured_at'] != record['captured_at']:
                raise ValueError('Persistence verification failed')
            totals = {field: sum(item[field] for item in record['items'])
                      for field in ('completed_count', 'completed_duration_seconds',
                                    'leader_review_duration_seconds', 'auditor_review_duration_seconds',
                                    'admin_review_duration_seconds', 'error_review_duration_seconds')}
            rows.append({'date': day, 'accounts': len(record['items']), **totals})
            print(json.dumps(rows[-1]), flush=True)
        except Exception as error:
            failed.append({'date': day, 'error': type(error).__name__})
            print(json.dumps(failed[-1]), flush=True)
    destination = Path('data/reports/efficiency-backfill.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'source': manager.scraper._base_url + '/api/dashboard/annotator-efficiency',
                                     'start': args.start, 'end': args.end, 'timezone': 'Asia/Shanghai',
                                     'rows': rows, 'failed': failed}, indent=2), encoding='utf-8')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
