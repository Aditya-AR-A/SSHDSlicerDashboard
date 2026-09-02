"""
Selenium-based scraper for the dashboard.

Fallback extraction method used when the dashboard requires JavaScript rendering.
This scraper uses a headless browser to:
1. Navigate to the dashboard
2. Enter credentials and submit login
3. Wait for the slicing table to load
4. Extract table data (handling pagination and dynamic loading)
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any
from slicing_dashboard.config import RAW_DIR, Settings
from slicing_dashboard.models.schemas import ExtractionResult, RawRecord
from slicing_dashboard.scraper.base import BaseScraper
_selenium_available = False
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    _selenium_available = True
except ImportError:
    pass


class SeleniumScraper(BaseScraper):
    """Browser-based dashboard scraper using Selenium WebDriver.

    This scraper is used when the dashboard renders data via JavaScript
    and cannot be accessed through simple HTTP requests.

    The exact selectors and navigation flow will be determined
    during Phase 1 discovery.
    """
    DEFAULT_SELECTORS: dict[str, str] = {'username_input':
        "input[type='text'], input[name='username']", 'password_input':
        "input[type='password']", 'login_button':
        "button[type='submit'], input[type='submit']", 'dashboard_table':
        'table', 'pagination_next': '.next, .pagination a:last-child'}

    def __init__(self, settings: (Settings | None)=None, headless: bool=
        True, selectors: (dict[str, str] | None)=None) ->None:
        if not _selenium_available:
            raise ImportError(
                "Selenium is not installed. Install it with: uv pip install 'slicing-dashboard[browser]'"
                )
        super().__init__(settings)
        self._headless = headless
        self._selectors = {**self.DEFAULT_SELECTORS, **(selectors or {})}
        self._driver: webdriver.Chrome | None = None
        self._wait_timeout = 15

    def _init_driver(self) ->None:
        """Initialize Chrome WebDriver."""
        options = ChromeOptions()
        if self._headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument(
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
        self._driver = webdriver.Chrome(options=options)
        self._driver.implicitly_wait(5)

    def login(self) ->bool:
        """Authenticate via browser form submission."""
        if not self._validate_credentials():
            return False
        if not self._driver:
            self._init_driver()
        assert self._driver is not None
        try:
            self._driver.get(self.settings.dashboard_url)
            wait = WebDriverWait(self._driver, self._wait_timeout)
            username_input = wait.until(EC.presence_of_element_located((By.
                CSS_SELECTOR, self._selectors['username_input'])))
            username_input.clear()
            username_input.send_keys(self.settings.admin_username)
            password_input = self._driver.find_element(By.CSS_SELECTOR,
                self._selectors['password_input'])
            password_input.clear()
            password_input.send_keys(self.settings.admin_password)
            login_button = self._driver.find_element(By.CSS_SELECTOR, self.
                _selectors['login_button'])
            login_button.click()
            try:
                wait.until(EC.any_of(EC.presence_of_element_located((By.
                    CSS_SELECTOR, self._selectors['dashboard_table'])), EC.
                    url_changes(self.settings.dashboard_url)))
            except Exception as e:
                pass
            page_source = self._driver.page_source.lower()
            if any(indicator in page_source for indicator in [
                'login failed', 'invalid', 'error']):
                return False
            self._authenticated = True
            return True
        except Exception as e:
            pass
            self._save_screenshot('login_failure')
            return False

    def discover(self) ->dict[str, Any]:
        """Discover dashboard structure using the browser.

        Opens the dashboard, inspects the DOM, and documents
        the login flow, table schema, and navigation.
        """
        discovery: dict[str, Any] = {'login': {}, 'dashboard': {}, 'table':
            {}, 'api_endpoints': [], 'extraction_method': 'selenium',
            'selenium_required': True, 'errors': []}
        if not self._driver:
            self._init_driver()
        assert self._driver is not None
        try:
            self._driver.get(self.settings.dashboard_url)
            wait = WebDriverWait(self._driver, self._wait_timeout)
            self._save_raw_html(self._driver.page_source, 'discovery_page')
            self._save_screenshot('discovery_page')
            discovery['login']['url'] = self._driver.current_url
            discovery['login']['title'] = self._driver.title
            forms = self._driver.find_elements(By.TAG_NAME, 'form')
            discovery['login']['forms_found'] = len(forms)
            if forms:
                inputs = forms[0].find_elements(By.TAG_NAME, 'input')
                discovery['login']['inputs'] = [{'name': inp.get_attribute(
                    'name'), 'type': inp.get_attribute('type'), 'id': inp.
                    get_attribute('id'), 'placeholder': inp.get_attribute(
                    'placeholder')} for inp in inputs]
            discovery['dashboard']['frameworks'] = self._detect_js_frameworks()
            discovery['api_endpoints'] = self._capture_network_requests()
        except Exception as e:
            pass
            error_msg = f'Discovery failed: {e}'
            discovery['errors'].append(error_msg)
        return discovery

    def extract(self) ->ExtractionResult:
        """Extract records from the slicing table using the browser."""
        if not self._authenticated:
            return ExtractionResult(success=False, errors=['Not authenticated']
                )
        assert self._driver is not None
        records: list[RawRecord] = []
        errors: list[str] = []
        warnings: list[str] = []
        pages_extracted = 0
        try:
            wait = WebDriverWait(self._driver, self._wait_timeout)
            table = wait.until(EC.presence_of_element_located((By.
                CSS_SELECTOR, self._selectors['dashboard_table'])))
            while True:
                pages_extracted += 1
                page_records = self._extract_current_page(pages_extracted)
                records.extend(page_records)
                if not self._go_to_next_page():
                    break
            self._save_raw_html(self._driver.page_source, 'extraction_final')
        except Exception as e:
            pass
            error_msg = f'Extraction failed: {e}'
            errors.append(error_msg)
            self._save_screenshot('extraction_failure')
        return ExtractionResult(success=len(records) > 0 and len(errors) ==
            0, records=records, total_records=len(records), pages_extracted
            =pages_extracted, extracted_at=datetime.now(), errors=errors,
            warnings=warnings)

    def close(self) ->None:
        """Close the browser."""
        if self._driver:
            self._driver.quit()
            self._driver = None

    def _extract_current_page(self, page_num: int) ->list[RawRecord]:
        """Extract records from the currently visible table page."""
        assert self._driver is not None
        records: list[RawRecord] = []
        table = self._driver.find_element(By.CSS_SELECTOR, self._selectors[
            'dashboard_table'])
        headers = [th.text.strip() for th in table.find_elements(By.
            TAG_NAME, 'th')]
        if not headers:
            first_row = table.find_elements(By.TAG_NAME, 'tr')
            if first_row:
                headers = [td.text.strip() for td in first_row[0].
                    find_elements(By.TAG_NAME, 'td')]
        rows = table.find_elements(By.TAG_NAME, 'tr')
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.find_elements(By.
                TAG_NAME, 'td')]
            if cells and len(cells) == len(headers):
                row_data = dict(zip(headers, cells))
                records.append(self._create_raw_record(row_data, page=page_num)
                    )
        return records

    def _go_to_next_page(self) ->bool:
        """Try to navigate to the next page. Returns False if no next page."""
        assert self._driver is not None
        try:
            next_btn = self._driver.find_element(By.CSS_SELECTOR, self.
                _selectors['pagination_next'])
            if next_btn.is_enabled() and next_btn.is_displayed():
                next_btn.click()
                WebDriverWait(self._driver, self._wait_timeout).until(EC.
                    staleness_of(self._driver.find_element(By.CSS_SELECTOR,
                    self._selectors['dashboard_table'])))
                WebDriverWait(self._driver, self._wait_timeout).until(EC.
                    presence_of_element_located((By.CSS_SELECTOR, self.
                    _selectors['dashboard_table'])))
                return True
        except Exception:
            pass
            pass
        return False

    def _detect_js_frameworks(self) ->list[str]:
        """Detect JavaScript frameworks used by the dashboard."""
        assert self._driver is not None
        frameworks: list[str] = []
        checks = {'Vue.js':
            "return !!window.Vue || !!document.querySelector('[data-v-]')",
            'React':
            'return !!window.React || !!window.__REACT_DEVTOOLS_GLOBAL_HOOK__',
            'Angular':
            "return !!window.ng || !!document.querySelector('[ng-app]')",
            'jQuery': 'return !!window.jQuery', 'Element UI':
            "return !!document.querySelector('.el-')", 'Ant Design':
            "return !!document.querySelector('.ant-')"}
        for name, script in checks.items():
            try:
                if self._driver.execute_script(script):
                    frameworks.append(name)
            except Exception:
                pass
        return frameworks

    def _capture_network_requests(self) ->list[str]:
        """Attempt to capture XHR/API endpoints from performance logs."""
        assert self._driver is not None
        endpoints: list[str] = []
        try:
            logs = self._driver.execute_script(
                """
                var entries = performance.getEntriesByType('resource');
                return entries
                    .filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
                    .map(e => e.name);
                """
                )
            if logs:
                endpoints = list(set(logs))
        except Exception:
            pass
            pass
        return endpoints

    def _save_raw_html(self, html: str, label: str) ->None:
        """Save raw HTML snapshot."""
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filepath = RAW_DIR / f'{label}_{timestamp}.html'
        filepath.write_text(html, encoding='utf-8')

    def _save_screenshot(self, label: str) ->None:
        """Save a screenshot for debugging."""
        if not self._driver:
            return
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filepath = RAW_DIR / f'{label}_{timestamp}.png'
        self._driver.save_screenshot(str(filepath))
