"""
Dashboard extraction orchestrator.

Coordinates the scraper with raw data storage. Responsible for:
- Selecting the appropriate scraper (HTTP or Selenium)
- Running the extraction
- Saving raw snapshots with timestamps
- Returning structured data to the processing layer

This module does NOT perform cleaning or normalization.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any
import pandas as pd
from slicing_dashboard.config import RAW_DIR, Settings, get_settings
from slicing_dashboard.models.schemas import ExtractionResult
from slicing_dashboard.scraper.base import BaseScraper
from slicing_dashboard.scraper.http_scraper import HTTPScraper


class DashboardExtractor:
    """Orchestrates dashboard data extraction.

    Usage:
        extractor = DashboardExtractor()
        result = extractor.run()  # Authenticate, extract, save
    """

    def __init__(self, settings: (Settings | None)=None, scraper: (
        BaseScraper | None)=None) ->None:
        self.settings = settings or get_settings()
        self._scraper = scraper

    def _get_scraper(self) ->BaseScraper:
        """Get or create the appropriate scraper.

        Tries HTTP first. If discovery shows Selenium is needed,
        switches to SeleniumScraper.
        """
        if self._scraper:
            return self._scraper
        self._scraper = HTTPScraper(self.settings)
        return self._scraper

    def discover(self) ->dict[str, Any]:
        """Run Phase 1 dashboard discovery.

        Returns:
            Discovery results documenting the dashboard structure.
        """
        scraper = self._get_scraper()
        discovery = scraper.discover()
        self._save_discovery(discovery)
        if discovery.get('selenium_required'):
            try:
                from slicing_dashboard.scraper.selenium_scraper import SeleniumScraper
                with SeleniumScraper(self.settings) as selenium_scraper:
                    selenium_discovery = selenium_scraper.discover()
                    discovery['selenium_discovery'] = selenium_discovery
                    self._save_discovery(selenium_discovery, label='selenium')
            except ImportError:
                discovery['selenium_discovery'] = {'error':
                    'Selenium not installed'}
        return discovery

    def run(self) ->ExtractionResult:
        """Execute a full extraction: authenticate → extract → save.

        Returns:
            ExtractionResult with all extracted records.

        Raises:
            RuntimeError: If credentials are missing or login fails.
        """
        if not self.settings.has_credentials:
            raise RuntimeError(
                'Admin credentials not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD in .env'
                )
        scraper = self._get_scraper()
        if not scraper.login():
            raise RuntimeError('Dashboard login failed')
        result = scraper.extract()
        if result.success and result.records:
            self._save_raw_snapshot(result)
        scraper.close()
        return result

    def _save_raw_snapshot(self, result: ExtractionResult) ->None:
        """Save the raw extraction as a timestamped snapshot.

        Creates a dated subdirectory: data/raw/YYYY-MM-DD/
        Never overwrites existing snapshots.
        """
        today = datetime.now().strftime('%Y-%m-%d')
        snapshot_dir = RAW_DIR / today
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw_data = [record.raw_data for record in result.records]
        df = pd.DataFrame(raw_data)
        csv_path = snapshot_dir / 'slicing.csv'
        if csv_path.exists():
            timestamp = datetime.now().strftime('%H%M%S')
            csv_path = snapshot_dir / f'slicing_{timestamp}.csv'
        json_path = csv_path.with_suffix('.json')
        json_data = {'extracted_at': result.extracted_at.isoformat(),
            'total_records': result.total_records, 'pages_extracted':
            result.pages_extracted, 'records': raw_data, 'metadata': result
            .metadata}
        import json
        with open(json_path, 'w') as f:
            json.dump(json_data, f, indent=2, default=str)
        df.to_csv(csv_path, index=False)

    def _save_discovery(self, discovery: dict[str, Any], label: str='http'
        ) ->None:
        """Save discovery results to the raw directory."""
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filepath = RAW_DIR / f'discovery_{label}_{timestamp}.json'
