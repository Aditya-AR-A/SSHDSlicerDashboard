"""
Data normalization.

Converts raw string values into structured types:
- Duration strings → seconds (float) + display format (HH:MM:SS)
- Date strings → pandas datetime + derived period columns
- Status values → canonical enum values

All normalization is applied AFTER cleaning (cleaning.py).
"""
from __future__ import annotations
from typing import Any
import pandas as pd
from slicing_dashboard.extraction.parsers import format_duration, parse_duration_string


def normalize_dataframe(df: pd.DataFrame, duration_column: (str | None)=
    None, date_column: (str | None)=None, status_column: (str | None)=None,
    timezone: str='Asia/Kolkata', requests: (list[dict[str, Any]] | None)=None
    ) ->tuple[pd.DataFrame, list[str]]:
    """Apply all normalizations to a cleaned DataFrame.

    Args:
        df: Cleaned DataFrame from the cleaning pipeline.
        duration_column: Column containing duration values to normalize.
        date_column: Column containing date/timestamp values to normalize.
        status_column: Column containing status values to normalize.
        timezone: Canonical timezone for date normalization.
        requests: List of system requests (e.g. for rework tracking).

    Returns:
        Tuple of (normalized DataFrame, list of warning messages).
    """
    warnings: list[str] = []
    if df.empty:
        warnings.append('Input DataFrame is empty')
        return df, warnings
    if duration_column is None:
        duration_column = _detect_duration_column(df)
    if duration_column and duration_column in df.columns:
        df, dur_warnings = normalize_durations(df, duration_column)
        warnings.extend(dur_warnings)
    if date_column is None:
        date_column = _detect_date_column(df)
    if date_column and date_column in df.columns:
        df, date_warnings = normalize_dates(df, date_column, timezone)
        warnings.extend(date_warnings)
    if status_column is None:
        status_column = _detect_status_column(df)
    if status_column and status_column in df.columns:
        df, status_warnings = normalize_status(df, status_column, requests)
        warnings.extend(status_warnings)
    return df, warnings


def normalize_durations(df: pd.DataFrame, column: str) ->tuple[pd.DataFrame,
    list[str]]:
    """Convert duration strings to seconds and display format.

    Creates two new columns:
    - duration_seconds: float representation in seconds
    - duration_display: formatted as HH:MM:SS

    Args:
        df: DataFrame containing the duration column.
        column: Name of the column with raw duration strings.

    Returns:
        Tuple of (DataFrame with new columns, warnings).
    """
    warnings: list[str] = []
    df['duration_seconds'] = df[column].apply(parse_duration_string)
    zero_count = (df['duration_seconds'] <= 0).sum()
    if zero_count > 0:
        warnings.append(f'{zero_count} records have zero or negative duration')
    df['duration_display'] = df['duration_seconds'].apply(format_duration)
    return df, warnings


def normalize_dates(df: pd.DataFrame, column: str, timezone: str='Asia/Kolkata'
    ) ->tuple[pd.DataFrame, list[str]]:
    """Convert date strings to pandas datetime and derive period columns.

    Creates:
    - completed_at: pandas datetime
    - completed_date: YYYY-MM-DD string
    - completed_week: ISO week as YYYY-Www
    - completed_month: YYYY-MM string

    Args:
        df: DataFrame containing the date column.
        column: Name of the column with raw date/time strings.
        timezone: Target timezone for normalization.

    Returns:
        Tuple of (DataFrame with new columns, warnings).
    """
    warnings: list[str] = []
    df['completed_at'] = pd.to_datetime(df[column], errors='coerce')
    invalid_count = df['completed_at'].isna().sum()
    original_na = df[column].isna().sum() + (df[column].astype(str).str.
        strip() == '').sum()
    new_invalid = invalid_count - original_na
    if new_invalid > 0:
        warnings.append(f'{new_invalid} records have invalid/unparseable dates'
            )
    if df['completed_at'].dt.tz is None:
        df['completed_at'] = df['completed_at'].dt.tz_localize(timezone,
            ambiguous='NaT')
    else:
        df['completed_at'] = df['completed_at'].dt.tz_convert(timezone)
    df['completed_date'] = df['completed_at'].dt.strftime('%Y-%m-%d').fillna(''
        )
    df['completed_week'] = df['completed_at'].dt.strftime('%G-W%V').fillna('')
    df['completed_month'] = df['completed_at'].dt.strftime('%Y-%m').fillna('')
    missing_count = df['completed_at'].isna().sum()
    if missing_count > 0:
        warnings.append(
            f'{missing_count} records have missing completion dates')
    return df, warnings


def normalize_status(df: pd.DataFrame, column: str, requests: (list[dict[
    str, Any]] | None)=None) ->tuple[pd.DataFrame, list[str]]:
    """Normalize status values to canonical forms and classify completion.

    Classifies:
    - completed normal: slice_completed
    - completed error: video_error_confirmed
    - pending/other: all other statuses
    """
    warnings: list[str] = []
    STATUS_MAP = {'slice_completed': 'slice_completed',
        'video_error_confirmed': 'video_error_confirmed',
        'slice_pending_admin_review': 'slice_pending_admin_review',
        'slice_pending_auditor_review': 'slice_pending_auditor_review',
        'slice_submitted': 'slice_submitted', 'slice_rework':
        'slice_rework', 'slice_assigned': 'slice_assigned', 'slicing':
        'slicing', 'video_error_review': 'video_error_review'}
    df['status_normalized'] = df[column].astype(str).str.strip().str.lower(
        ).map(STATUS_MAP).fillna('unknown')
    df['is_completed'] = df['status_normalized'].isin(['slice_completed',
        'video_error_confirmed'])
    df['completion_type'] = 'none'
    df.loc[df['status_normalized'] == 'slice_completed', 'completion_type'
        ] = 'normal'
    df.loc[df['status_normalized'] == 'video_error_confirmed',
        'completion_type'] = 'error'
    rework_by_map = {}
    if requests:
        sorted_reqs = sorted([r for r in requests if r.get('request_type') ==
            'rework_notice'], key=lambda x: x.get('created_at', ''))
        for req in sorted_reqs:
            tid = req.get('task_id')
            if tid:
                returned_by_name = req.get('returned_by_name') or ''
                name_lower = returned_by_name.lower()
                if 'auditor' in name_lower or 'qa' in name_lower:
                    rework_by_map[tid] = 'auditor'
                else:
                    rework_by_map[tid] = 'leader'
    if 'id' in df.columns:
        df['rework_by'] = df['id'].map(rework_by_map).fillna('')
    else:
        df['rework_by'] = ''
    unknown_count = (df['status_normalized'] == 'unknown').sum()
    if unknown_count > 0:
        unknown_values = df.loc[df['status_normalized'] == 'unknown', column
            ].unique()
        warnings.append(
            f'{unknown_count} records have unknown status values: {list(unknown_values)}'
            )
    return df, warnings


def _detect_duration_column(df: pd.DataFrame) ->(str | None):
    """Auto-detect the duration column by name heuristics."""
    keywords = ['duration', 'time', 'length', 'completed_duration', '时长']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in keywords):
            return col
    return None


def _detect_date_column(df: pd.DataFrame) ->(str | None):
    """Auto-detect the date/timestamp column by name heuristics."""
    keywords = ['completed_at', 'completed', 'date', 'timestamp', 'time',
        'created_at', 'finished_at', '完成时间']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in keywords):
            return col
    return None


def _detect_status_column(df: pd.DataFrame) ->(str | None):
    """Auto-detect the status column by name heuristics."""
    keywords = ['status', 'state', '状态']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in keywords):
            return col
    return None
