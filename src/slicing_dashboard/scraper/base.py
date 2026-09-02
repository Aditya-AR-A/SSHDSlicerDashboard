"""
Abstract base scraper.

Defines the interface that all scraper implementations must follow.
This keeps the extraction layer cleanly separated from processing and reporting.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from slicing_dashboard.config import Settings, get_settings
from slicing_dashboard.models.schemas import ExtractionResult, RawRecord


class BaseScraper(ABC):
    """Abstract base class for dashboard scrapers.

    All scrapers must implement:
    - login(): Authenticate with the dashboard
    - discover(): Inspect the dashboard structure (Phase 1)
    - extract(): Extract records from the slicing table
    - close(): Clean up resources
    """

    def __init__(self, settings: (Settings | None)=None) ->None:
        self.settings = settings or get_settings()
        self._authenticated = False

    @abstractmethod
    def login(self) ->bool:
        """Authenticate with the dashboard.

        Returns:
            True if login was successful, False otherwise.
        """

    @abstractmethod
    def discover(self) ->dict[str, Any]:
        """Discover dashboard structure (Phase 1).

        Returns:
            Dictionary documenting the dashboard structure:
            - login_url, form selectors, success/failure indicators
            - dashboard_url, navigation path
            - table schema (columns, types)
            - pagination mechanism
            - filters and date selectors
            - api endpoints (if found)
        """

    @abstractmethod
    def extract(self) ->ExtractionResult:
        """Extract all records from the slicing dashboard table.

        Must handle:
        - Pagination (all pages)
        - Dynamic loading
        - Error conditions (empty table, missing data)

        Returns:
            ExtractionResult containing all extracted records.
        """

    @abstractmethod
    def close(self) ->None:
        """Clean up resources (close browser, sessions, etc.)."""

    @property
    def is_authenticated(self) ->bool:
        """Whether the scraper has an active authenticated session."""
        return self._authenticated

    def __enter__(self) ->BaseScraper:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) ->None:
        self.close()

    def _validate_credentials(self) ->bool:
        """Check that credentials are configured."""
        if not self.settings.has_credentials:
            return False
        return True

    def _create_raw_record(self, data: dict[str, Any], page: int=1
        ) ->RawRecord:
        """Create a RawRecord from extracted data."""
        return RawRecord(raw_data=data, extracted_at=datetime.now(),
            source_page=page)
