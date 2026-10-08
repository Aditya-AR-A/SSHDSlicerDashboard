"""Read-only daily submission audit; saves diagnostic evidence, never report history."""
import argparse
from datetime import date
import json
from pathlib import Path

from slicing_dashboard.config import get_settings, PROJECT_ROOT
from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.db import DatabaseManager
from slicing_dashboard.processing.daily_work import aggregate_daily_work, format_video_seconds
from slicing_dashboard.processing.daily_work_source import DailyWorkSource
from slicing_dashboard.reporting.dashboard_reports import aggregate_observations, day_for_ui
from slicing_dashboard.scraper.http_scraper import HTTPScraper


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('day', type=date.fromisoformat)
    args = parser.parse_args()
    day = args.day.isoformat()
    settings = get_settings()
    manager = DataManager.__new__(DataManager)
    mapping_path = Path(settings.user_mapping_path)
    if not mapping_path.is_absolute():
        mapping_path = PROJECT_ROOT / mapping_path
    manager.user_mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    database = DatabaseManager()
    mappings = database.load_user_mappings()
    if mappings:
        manager.user_mapping_full = {row['id']: row for row in mappings}
        manager.user_mapping = {row['id']: row['mapped_user'] for row in mappings}
    scraper = HTTPScraper(settings)
    try:
        source = DailyWorkSource(scraper, manager._get_reporting_name).fetch(day)
        frame, tasks = aggregate_daily_work(source['tasks'], source['returned_accounts'], source['reviews'],
            day, manager._get_reporting_name, requests=source.get('requests', []))
        stored_path = PROJECT_ROOT / 'data/reports/daily-work' / f'{day}.json'
        saved = json.loads(stored_path.read_text(encoding='utf-8')) if stored_path.exists() else {}
        saved_rows = day_for_ui(saved, day, manager._get_reporting_name)['rows'] if saved else []
        # Diagnostic only: keep disappeared submissions from the saved observation.
        observed = {str(task['task_id']): task for task in saved.get('tasks', [])}
        observed.update({str(task['task_id']): task for task in tasks})
        union = aggregate_observations(list(observed.values()), manager._get_reporting_name)
        def hours(rows):
            return {row['User']: format_video_seconds(row['Total Duration']) for row in rows}
        report = {'date': day, 'captured_at': source['captured_at'], 'saved_captured_at': saved.get('metadata', {}).get('captured_at'),
                  'inventory_tasks': len(source['tasks']), 'submitted_tasks': len(tasks),
                  'saved_totals': hours(saved_rows), 'latest_source_totals': hours(frame.to_dict('records')),
                  'retained_plus_latest_totals': hours(union), 'tasks': tasks,
                  'mapping_source': 'configured database' if mappings else 'local account mapping'}
        target = PROJECT_ROOT / 'data/reports/chart-qa' / f'daily-api-{day}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({key: value for key, value in report.items() if key != 'tasks'}))
    finally:
        scraper._client.close()
        if database.mongo_client is not None:
            database.mongo_client.close()


if __name__ == '__main__':
    main()
