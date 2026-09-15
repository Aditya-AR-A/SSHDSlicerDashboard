"""
HTML and data parsers for dashboard content.

Utilities for parsing various data formats found in the dashboard:
- HTML tables
- JSON API responses
- Duration strings
- Date strings
"""
from __future__ import annotations
import re
from typing import Any
from bs4 import BeautifulSoup, Tag


def parse_html_table(html: str, table_index: int=0) ->list[dict[str, str]]:
    """Parse an HTML table into a list of row dictionaries.

    Args:
        html: HTML content containing a table.
        table_index: Which table to parse (0-indexed) if multiple exist.

    Returns:
        List of dicts mapping column headers to cell values.
    """
    soup = BeautifulSoup(html, 'lxml')
    tables = soup.find_all('table')
    if not tables:
        return []
    if table_index >= len(tables):
        return []
    table = tables[table_index]
    return _extract_table_data(table)


def parse_all_tables(html: str) ->list[list[dict[str, str]]]:
    """Parse all tables from HTML content.

    Returns:
        List of tables, each being a list of row dictionaries.
    """
    soup = BeautifulSoup(html, 'lxml')
    tables = soup.find_all('table')
    return [_extract_table_data(table) for table in tables]


def find_largest_table(html: str) ->list[dict[str, str]]:
    """Find and parse the table with the most rows.

    Useful when the target table isn't at a known index.
    """
    soup = BeautifulSoup(html, 'lxml')
    tables = soup.find_all('table')
    if not tables:
        return []
    largest = max(tables, key=lambda t: len(t.find_all('tr')))
    return _extract_table_data(largest)


def extract_table_headers(html: str, table_index: int=0) ->list[str]:
    """Extract just the column headers from a table."""
    soup = BeautifulSoup(html, 'lxml')
    tables = soup.find_all('table')
    if not tables or table_index >= len(tables):
        return []
    table = tables[table_index]
    return _get_headers(table)


def parse_duration_string(duration_str: Any) ->float:
    """Parse various duration formats into total seconds."""
    if isinstance(duration_str, (int, float)):
        return float(duration_str)
    if not duration_str or not isinstance(duration_str, str
        ) or not duration_str.strip():
        return 0.0
    s = duration_str.strip()
    match = re.match('^(\\d+):(\\d{1,2}):(\\d{1,2})$', s)
    if match:
        h, m, sec = int(match.group(1)), int(match.group(2)), int(match.
            group(3))
        return h * 3600 + m * 60 + sec
    match = re.match('^(\\d+):(\\d{1,2})$', s)
    if match:
        m, sec = int(match.group(1)), int(match.group(2))
        return m * 60 + sec
    match = re.match('^(\\d+\\.?\\d*)$', s)
    if match:
        return float(match.group(1)) * 60
    match = re.match('^(\\d+\\.?\\d*)\\s*min(?:utes?)?$', s, re.IGNORECASE)
    if match:
        return float(match.group(1)) * 60
    match = re.match('^(\\d+)\\s*h(?:ours?)?\\s*(\\d+)\\s*m(?:in(?:utes?)?)?$',
        s, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60
    match = re.match('^(\\d+\\.?\\d*)\\s*h(?:ours?)?$', s, re.IGNORECASE)
    if match:
        return float(match.group(1)) * 3600
    match = re.match('^(\\d+\\.?\\d*)\\s*s(?:ec(?:onds?)?)?$', s, re.IGNORECASE
        )
    if match:
        return float(match.group(1))
    return 0.0


def format_duration(seconds: float) ->str:
    """Format duration in seconds to HH:MM:SS display string.

    Args:
        seconds: Total duration in seconds.

    Returns:
        Formatted string like "05:08:00" or "01:45:20".
    """
    if seconds is None:
        return '00:00:00'
    try:
        import math
        if math.isnan(seconds) or seconds < 0:
            return '00:00:00'
    except (TypeError, ValueError):
        return '00:00:00'
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = total_seconds % 3600 // 60
    secs = total_seconds % 60
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def _get_headers(table: Tag) ->list[str]:
    """Extract column headers from a table element."""
    headers = [th.get_text(strip=True) for th in table.find_all('th')]
    if not headers:
        first_row = table.find('tr')
        if first_row:
            headers = [td.get_text(strip=True) for td in first_row.find_all
                ('td')]
    return headers


def _extract_table_data(table: Tag) ->list[dict[str, str]]:
    """Extract all rows from a table as list of dicts."""
    headers = _get_headers(table)
    if not headers:
        return []
    rows: list[dict[str, str]] = []
    all_trs = table.find_all('tr')
    start_idx = 1 if all_trs else 0
    for tr in all_trs[start_idx:]:
        cells = [td.get_text(strip=True) for td in tr.find_all('td')]
        if not cells:
            continue
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
        else:
            padded = cells + [''] * max(0, len(headers) - len(cells))
            rows.append(dict(zip(headers, padded[:len(headers)])))
    return rows
