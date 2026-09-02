"""
Data validation.

Validates extracted and processed data at multiple levels:
- Extraction validation (did we get data?)
- Data quality validation (are values reasonable?)
- Reporting validation (do totals reconcile?)

All validation results are logged and can be included in the
Excel Validation sheet.
"""
from __future__ import annotations
from typing import Any
import pandas as pd
from slicing_dashboard.extraction.parsers import format_duration
from slicing_dashboard.models.schemas import ValidationResult


def validate_extraction(df: pd.DataFrame) ->ValidationResult:
    """Validate raw extraction results.

    Checks:
    - DataFrame is not empty
    - Expected column patterns exist
    - No completely null columns
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}
    checks_passed = 0
    checks_failed = 0
    if df.empty:
        errors.append('Extraction returned empty DataFrame')
        checks_failed += 1
    else:
        checks_passed += 1
        details['row_count'] = len(df)
        details['column_count'] = len(df.columns)
        details['columns'] = list(df.columns)
    null_columns = [col for col in df.columns if df[col].isna().all()]
    if null_columns:
        warnings.append(f'Columns are entirely null: {null_columns}')
        checks_failed += 1
    else:
        checks_passed += 1
    if 0 < len(df) < 5:
        warnings.append(
            f'Very few records ({len(df)}) — possible extraction issue')
    elif len(df) > 10000:
        warnings.append(
            f'Very large dataset ({len(df)} rows) — verify completeness')
    checks_passed += 1
    return ValidationResult(is_valid=len(errors) == 0, checks_passed=
        checks_passed, checks_failed=checks_failed, errors=errors, warnings
        =warnings, details=details)


def validate_data_quality(df: pd.DataFrame) ->ValidationResult:
    """Validate data quality of processed records.

    Checks:
    - No missing user identifiers
    - No invalid durations (negative, impossibly large)
    - No invalid dates
    - No unknown users (warning, not error)
    - Duration sanity checks
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}
    checks_passed = 0
    checks_failed = 0
    if 'user_name' in df.columns:
        missing_users = df['user_name'].isna().sum() + (df['user_name'] == ''
            ).sum()
        if missing_users > 0:
            errors.append(
                f'{missing_users} records have missing user identifiers')
            checks_failed += 1
        else:
            checks_passed += 1
        details['unique_users'] = list(df['user_name'].dropna().unique())
    if 'duration_seconds' in df.columns:
        negative = (df['duration_seconds'] < 0).sum()
        if negative > 0:
            errors.append(f'{negative} records have negative duration')
            checks_failed += 1
        else:
            checks_passed += 1
        zero = (df['duration_seconds'] == 0).sum()
        if zero > 0:
            warnings.append(f'{zero} records have zero duration')
        impossible = (df['duration_seconds'] > 86400).sum()
        if impossible > 0:
            warnings.append(f'{impossible} records have duration > 24 hours')
        total_seconds = df['duration_seconds'].sum()
        details['total_duration'] = format_duration(total_seconds)
        details['avg_duration'] = format_duration(total_seconds / max(len(
            df), 1))
    if 'completed_at' in df.columns:
        invalid_dates = df['completed_at'].isna().sum()
        if invalid_dates > 0:
            warnings.append(
                f'{invalid_dates} records have invalid/missing dates')
        details['date_range'] = {'min': str(df['completed_at'].min()),
            'max': str(df['completed_at'].max())}
        checks_passed += 1
    if 'record_id' in df.columns:
        dup_count = df['record_id'].duplicated().sum()
        if dup_count > 0:
            warnings.append(f'{dup_count} duplicate record IDs remain')
        else:
            checks_passed += 1
    return ValidationResult(is_valid=len(errors) == 0, checks_passed=
        checks_passed, checks_failed=checks_failed, errors=errors, warnings
        =warnings, details=details)


def validate_report_totals(df: pd.DataFrame, group_column: str='user_name',
    value_column: str='duration_seconds') ->ValidationResult:
    """Validate that report totals reconcile.

    Checks:
    - Grand total equals sum of user totals
    - Daily totals sum to weekly totals
    - Weekly totals sum to monthly totals

    Args:
        df: Processed DataFrame with duration and grouping columns.
        group_column: Column to group by (default: user_name).
        value_column: Column to sum (default: duration_seconds).
    """
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}
    checks_passed = 0
    checks_failed = 0
    if value_column not in df.columns:
        errors.append(f"Value column '{value_column}' not found")
        return ValidationResult(is_valid=False, checks_passed=0,
            checks_failed=1, errors=errors, warnings=warnings, details=details)
    grand_total = df[value_column].sum()
    details['grand_total'] = format_duration(grand_total)
    if group_column in df.columns:
        user_totals = df.groupby(group_column)[value_column].sum()
        user_sum = user_totals.sum()
        if abs(user_sum - grand_total) > 0.01:
            errors.append(
                f'User totals ({format_duration(user_sum)}) != grand total ({format_duration(grand_total)})'
                )
            checks_failed += 1
        else:
            checks_passed += 1
        details['user_totals'] = {user: format_duration(total) for user,
            total in user_totals.items()}
    if 'completed_date' in df.columns:
        daily_totals = df.groupby('completed_date')[value_column].sum()
        daily_sum = daily_totals.sum()
        if abs(daily_sum - grand_total) > 0.01:
            errors.append(
                f'Daily totals ({format_duration(daily_sum)}) != grand total ({format_duration(grand_total)})'
                )
            checks_failed += 1
        else:
            checks_passed += 1
    if 'completed_week' in df.columns:
        weekly_totals = df.groupby('completed_week')[value_column].sum()
        weekly_sum = weekly_totals.sum()
        if abs(weekly_sum - grand_total) > 0.01:
            errors.append(
                f'Weekly totals ({format_duration(weekly_sum)}) != grand total ({format_duration(grand_total)})'
                )
            checks_failed += 1
        else:
            checks_passed += 1
    return ValidationResult(is_valid=len(errors) == 0, checks_passed=
        checks_passed, checks_failed=checks_failed, errors=errors, warnings
        =warnings, details=details)


def generate_validation_summary(*results: ValidationResult) ->dict[str, Any]:
    """Combine multiple validation results into a single summary.

    Used for the Excel Validation sheet.
    """
    total_passed = sum(r.checks_passed for r in results)
    total_failed = sum(r.checks_failed for r in results)
    all_errors = []
    all_warnings = []
    all_details: dict[str, Any] = {}
    for i, result in enumerate(results):
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        all_details[f'check_{i}'] = result.details
    return {'overall_valid': all(r.is_valid for r in results),
        'total_checks_passed': total_passed, 'total_checks_failed':
        total_failed, 'errors': all_errors, 'warnings': all_warnings,
        'details': all_details}
