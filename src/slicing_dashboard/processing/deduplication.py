"""
Deduplication logic.

Removes duplicate records using deterministic identification.
Prefers task_id if available; falls back to composite keys.

Important: Does NOT use naive df.drop_duplicates() unless the schema
proves every complete row is uniquely identifiable.
"""
from __future__ import annotations
import hashlib
from typing import Any
import pandas as pd


def deduplicate(df: pd.DataFrame, id_column: (str | None)=None,
    composite_keys: (list[str] | None)=None, keep: str='last') ->tuple[pd.
    DataFrame, int, list[str]]:
    """Remove duplicate records deterministically.

    Strategy:
    1. If id_column is provided and exists, use it as the unique key.
    2. If composite_keys are provided, use their combination.
    3. Otherwise, generate a record_id hash from key columns.

    Args:
        df: DataFrame to deduplicate.
        id_column: Name of a unique identifier column (e.g., 'task_id').
        composite_keys: List of column names forming a composite unique key.
            Fallback: ['user_id', 'video_name', 'completed_at']
        keep: Which duplicate to keep ('first', 'last').

    Returns:
        Tuple of (deduplicated DataFrame, count of removed duplicates, warnings).
    """
    warnings: list[str] = []
    if df.empty:
        return df, 0, warnings
    original_count = len(df)
    if id_column and id_column in df.columns:
        df = df.drop_duplicates(subset=[id_column], keep=keep)
        removed = original_count - len(df)
        return df, removed, warnings
    if composite_keys:
        available_keys = [k for k in composite_keys if k in df.columns]
        if available_keys:
            df = df.drop_duplicates(subset=available_keys, keep=keep)
            removed = original_count - len(df)
            return df, removed, warnings
        else:
            warnings.append(
                f'None of the composite keys {composite_keys} found in columns'
                )
    key_columns = _detect_key_columns(df)
    if key_columns:
        df['record_id'] = df.apply(lambda row: _generate_record_id(row,
            key_columns), axis=1)
        df = df.drop_duplicates(subset=['record_id'], keep=keep)
        removed = original_count - len(df)
        return df, removed, warnings
    warnings.append(
        'No unique key identified — falling back to full-row deduplication. This may not be reliable.'
        )
    df = df.drop_duplicates(keep=keep)
    removed = original_count - len(df)
    return df, removed, warnings


def generate_record_ids(df: pd.DataFrame, key_columns: (list[str] | None)=None
    ) ->pd.DataFrame:
    """Generate deterministic record IDs for all rows.

    Args:
        df: DataFrame to add record IDs to.
        key_columns: Columns to use for ID generation.
            If None, auto-detects.

    Returns:
        DataFrame with 'record_id' column added.
    """
    if key_columns is None:
        key_columns = _detect_key_columns(df)
    if not key_columns:
        key_columns = list(df.columns)
    df['record_id'] = df.apply(lambda row: _generate_record_id(row,
        key_columns), axis=1)
    return df


def _detect_key_columns(df: pd.DataFrame) ->list[str]:
    """Auto-detect columns that form a reasonable composite key."""
    priority_groups = [['id'], ['task_id'], ['user_id', 'video_name',
        'completed_at'], ['user_name', 'video_name', 'completed_at'], [
        'user_id', 'task_id'], ['user_id', 'video_name', 'duration_seconds'
        ], ['user_name', 'video_name', 'duration_seconds']]
    for group in priority_groups:
        if all(col in df.columns for col in group):
            return group
    derived_cols = {'record_id', 'user_name', 'duration_seconds',
        'duration_display', 'completed_date', 'completed_week',
        'completed_month', 'status_normalized', 'source_extracted_at'}
    available = [col for col in df.columns if col not in derived_cols]
    if available:
        return available
    return []


def _generate_record_id(row: pd.Series, key_columns: list[str]) ->str:
    """Generate a deterministic hash ID from key column values."""
    parts = [str(row.get(col, '')) for col in key_columns]
    combined = '|'.join(parts)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()[:16]
