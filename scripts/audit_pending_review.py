"""Read-only comparison of a fresh source capture with every plotted user/stage."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from math import isclose
from pathlib import Path
from unittest.mock import patch

from slicing_dashboard.config import get_settings, PROJECT_ROOT
from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.plots.pending_chart import build_pending_chart
from slicing_dashboard.processing.pending_review import STAGES, current_review_end, fetch_items, pending_frame
from slicing_dashboard.scraper.http_scraper import HTTPScraper


def main():
    settings = get_settings()
    manager = DataManager.__new__(DataManager)
    manager.scraper = HTTPScraper(settings)
    path = Path(settings.user_mapping_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    manager.user_mapping = json.loads(path.read_text(encoding='utf-8'))
    try:
        items = fetch_items(manager.scraper, current_review_end())
        expected = defaultdict(lambda: [0., 0])
        for item in items:
            user = manager._get_reporting_name(item.get('user_id'), item['username'])
            if str(user).casefold() in {'admin', 'test', 'dep', 'exempt', 'user-none', '(unassigned)', ''}:
                continue
            for field, label in STAGES:
                expected[user, label][0] += float(item[f'{field}_review_duration_seconds'])
                expected[user, label][1] += int(item[f'{field}_review_count'])
        with patch('slicing_dashboard.processing.pending_review.fetch_items', return_value=items):
            frame = pending_frame(manager, True)
        figure = build_pending_chart(frame, True)
        displayed = {(user, trace.name): (float(hours) * 3600, int(custom[2]))
                     for trace in figure.data for user, hours, custom in zip(trace.x, trace.y, trace.customdata)}
        comparisons = []
        for (user, stage), (seconds, count) in sorted(expected.items()):
            plotted = displayed.get((user, stage), (0., 0))
            match = isclose(plotted[0], seconds, abs_tol=1e-6) and plotted[1] == count
            comparisons.append({'user': user, 'stage': stage, 'source_seconds': seconds, 'source_tasks': count,
                                'plotted_seconds': plotted[0], 'plotted_tasks': plotted[1], 'match': match})
        report = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source_accounts': len(items),
                  'mapping_source': 'local account mapping', 'comparisons': comparisons,
                  'all_match': all(row['match'] for row in comparisons)}
        target = PROJECT_ROOT / 'data/reports/chart-qa/pending-api-comparison.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'accounts': len(items), 'user_stage_comparisons': len(comparisons), 'all_match': report['all_match']}))
    except Exception as error:
        print(json.dumps({'available': False, 'error_type': type(error).__name__}))
        raise SystemExit(1) from None
    finally:
        manager.scraper._client.close()


if __name__ == '__main__':
    main()
