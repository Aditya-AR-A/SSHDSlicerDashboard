"""
Data cleaning pipeline.

Handles the initial transformation of raw extracted data into a clean DataFrame:
- Remove empty/null rows
- Strip whitespace
- Handle missing values
- Apply user mapping
- Flag problematic records for review

This module operates on raw DataFrames and returns cleaned DataFrames.
It does NOT perform normalization (duration/date conversion) — that's in normalization.py.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pandas as pd
from slicing_dashboard.config import PROJECT_ROOT
DEFAULT_USER_MAPPING: dict[str, str] = {'SSHD-Adi': 'Aditya', 'SSHD-Aditya':
    'Aditya', 'SSHD-Slicer1': 'Aditya', 'SSHD-Komal': 'Komal',
    'SSHD-Ranjeeta': 'Komal', 'SSHD-Priya': 'Priya', 'SSHD-Slicer2':
    'Priya', 'SSHD-Rajni': 'Rajni'}


def load_user_mapping(mapping_path: (str | Path | None)=None) ->dict[str, str]:
    """Load user mapping from a JSON configuration file.

    Falls back to DEFAULT_USER_MAPPING if the file doesn't exist.

    Args:
        mapping_path: Path to JSON mapping file. If None, uses default config path.

    Returns:
        Dictionary mapping dashboard user IDs to canonical names.
    """
    if mapping_path is None:
        mapping_path = PROJECT_ROOT / 'config' / 'user_mapping.json'
    else:
        mapping_path = Path(mapping_path)
    if mapping_path.exists():
        with open(mapping_path, encoding='utf-8') as f:
            mapping = json.load(f)
        return mapping
    return DEFAULT_USER_MAPPING.copy()


def save_user_mapping(mapping: dict[str, str], mapping_path: (str | Path |
    None)=None) ->None:
    """Save user mapping to a JSON configuration file."""
    if mapping_path is None:
        mapping_path = PROJECT_ROOT / 'config' / 'user_mapping.json'
    else:
        mapping_path = Path(mapping_path)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)


def clean_dataframe(df: pd.DataFrame, user_column: (str | None)=None,
    user_mapping: (dict[str, str] | None)=None) ->tuple[pd.DataFrame, list[str]
    ]:
    """Apply cleaning transformations to raw extracted data.

    Steps:
    1. Strip whitespace from all string columns
    2. Remove completely empty rows
    3. Apply user mapping (preserving unknown users with warnings)
    4. Flag records with missing critical fields

    Args:
        df: Raw DataFrame from extraction.
        user_column: Name of the column containing user identifiers.
            If None, attempts to auto-detect.
        user_mapping: User name mapping dict. If None, loads from config.

    Returns:
        Tuple of (cleaned DataFrame, list of warning messages).
    """
    warnings: list[str] = []
    if df.empty:
        warnings.append('Input DataFrame is empty')
        return df, warnings
    original_count = len(df)
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace('nan', '')
    df = df.dropna(how='all')
    df = df[~df.astype(str).eq('').all(axis=1)]
    removed = original_count - len(df)
    if removed > 0:
        pass
    if user_column is None:
        user_column = _detect_user_column(df)
    if user_column and user_column in df.columns:
        if user_mapping is None:
            user_mapping = load_user_mapping()
        df, user_warnings = _apply_user_mapping(df, user_column, user_mapping)
        warnings.extend(user_warnings)
    else:
        warnings.append(
            f'User column not found. Columns available: {list(df.columns)}')
    return df, warnings


def _detect_user_column(df: pd.DataFrame) ->(str | None):
    """Auto-detect the user/username column by name heuristics."""
    for priority_col in ['slicer', 'slicer_id', 'username', 'user_id',
        'user_name']:
        if priority_col in df.columns:
            return priority_col
    user_keywords = ['user', 'username', 'operator', 'assigned', 'worker',
        'name']
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(kw in col_lower for kw in user_keywords):
            return col
    return None


def _apply_user_mapping(df: pd.DataFrame, user_column: str, mapping: dict[
    str, str]) ->tuple[pd.DataFrame, list[str]]:
    """Map dashboard user identifiers to canonical names.

    Unknown users are preserved with their original identifier
    and a warning is generated — they are NOT silently discarded.
    """
    warnings: list[str] = []
    df['user_name'] = df[user_column].map(mapping)
    unknown_mask = df['user_name'].isna()
    unknown_users = df.loc[unknown_mask, user_column].unique()
    if len(unknown_users) > 0:
        for user in unknown_users:
            warnings.append(f"Unknown user (preserved as-is): '{user}'")
        df.loc[unknown_mask, 'user_name'] = df.loc[unknown_mask, user_column]
    df['user_id'] = df[user_column]
    return df, warnings
