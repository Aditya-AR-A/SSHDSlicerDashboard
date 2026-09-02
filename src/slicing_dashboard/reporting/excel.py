"""
Excel report writer.

Generates formatted Excel files with multiple worksheets using openpyxl.
Handles:
- Summary sheets
- User breakdown sheets
- Daily/Weekly/Monthly data sheets
- Raw data sheets
- Validation sheets

The Excel output is the primary user-facing artifact.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Any
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from slicing_dashboard.config import REPORTS_DIR
from slicing_dashboard.models.schemas import ReportSummary
HEADER_FONT = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
HEADER_FILL = PatternFill(start_color='2F5496', end_color='2F5496',
    fill_type='solid')
HEADER_ALIGNMENT = Alignment(horizontal='center', vertical='center',
    wrap_text=True)
TITLE_FONT = Font(name='Calibri', bold=True, size=14, color='2F5496')
SUBTITLE_FONT = Font(name='Calibri', bold=True, size=11, color='404040')
DATA_FONT = Font(name='Calibri', size=10)
DATA_ALIGNMENT = Alignment(horizontal='left', vertical='center')
NUMBER_ALIGNMENT = Alignment(horizontal='right', vertical='center')
TOTAL_FONT = Font(name='Calibri', bold=True, size=11)
TOTAL_FILL = PatternFill(start_color='D6E4F0', end_color='D6E4F0',
    fill_type='solid')
WARNING_FILL = PatternFill(start_color='FFF2CC', end_color='FFF2CC',
    fill_type='solid')
ERROR_FILL = PatternFill(start_color='F4CCCC', end_color='F4CCCC',
    fill_type='solid')
THIN_BORDER = Border(left=Side(style='thin'), right=Side(style='thin'), top
    =Side(style='thin'), bottom=Side(style='thin'))


def write_daily_report(report_data: dict[str, Any], output_dir: (str | Path |
    None)=None) ->Path:
    """Write a daily Excel report.

    Sheets:
    1. Summary — Overview with key metrics
    2. User Summary — User-wise breakdown
    3. Daily Data — All records for the day
    4. Validation — Data quality checks

    Returns:
        Path to the generated Excel file.
    """
    summary: ReportSummary = report_data['summary']
    user_data: pd.DataFrame = report_data['user_data']
    daily_data: pd.DataFrame = report_data.get('daily_data', pd.DataFrame())
    if not output_dir:
        try:
            dt = datetime.strptime(summary.period_label, '%Y-%m-%d')
            year = dt.strftime('%Y')
            month = dt.strftime('%m')
            day = dt.strftime('%d')
            output_dir = REPORTS_DIR / 'daily' / year / month / day
        except ValueError:
            output_dir = REPORTS_DIR / 'daily'
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f'Daily_Report_{summary.period_label}.xlsx'
    filepath = output_dir / filename
    wb = Workbook()
    _write_summary_sheet(wb, summary, 'Daily Report')
    if not user_data.empty:
        _write_dataframe_sheet(wb, user_data, 'User Summary', is_summary=True)
    if not daily_data.empty:
        display_cols = _select_display_columns(daily_data)
        _write_dataframe_sheet(wb, daily_data[display_cols], 'Daily Data')
    validation = report_data.get('validation')
    if validation:
        _write_validation_sheet(wb, validation)
    if len(wb.sheetnames) > 1 and 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    return filepath


def write_weekly_report(report_data: dict[str, Any], output_dir: (str |
    Path | None)=None) ->Path:
    """Write a weekly Excel report.

    Sheets:
    1. Summary — Overview
    2. User Summary — User-wise breakdown
    3. Daily Breakdown — Daily data within the week
    4. Raw Data — All records
    5. Validation
    """
    summary: ReportSummary = report_data['summary']
    user_data: pd.DataFrame = report_data['user_data']
    daily_breakdown: pd.DataFrame = report_data.get('daily_breakdown', pd.
        DataFrame())
    raw_data: pd.DataFrame = report_data.get('raw_data', pd.DataFrame())
    output_dir = Path(output_dir) if output_dir else REPORTS_DIR / 'weekly'
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f'Weekly_Report_{summary.period_label}.xlsx'
    filepath = output_dir / filename
    wb = Workbook()
    _write_summary_sheet(wb, summary, 'Weekly Report')
    if not user_data.empty:
        _write_dataframe_sheet(wb, user_data, 'User Summary', is_summary=True)
    if not daily_breakdown.empty:
        _write_dataframe_sheet(wb, daily_breakdown, 'Daily Breakdown')
    if not raw_data.empty:
        display_cols = _select_display_columns(raw_data)
        _write_dataframe_sheet(wb, raw_data[display_cols], 'Raw Data')
    validation = report_data.get('validation')
    if validation:
        _write_validation_sheet(wb, validation)
    if len(wb.sheetnames) > 1 and 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    return filepath


def write_monthly_report(report_data: dict[str, Any], output_dir: (str |
    Path | None)=None) ->Path:
    """Write a monthly Excel report.

    Sheets:
    1. Summary — Overview
    2. User Summary — User-wise breakdown
    3. Weekly Breakdown — Weekly data within the month
    4. Daily Breakdown — Daily data
    5. Raw Data — All records
    6. Validation
    """
    summary: ReportSummary = report_data['summary']
    user_data: pd.DataFrame = report_data['user_data']
    weekly_breakdown: pd.DataFrame = report_data.get('weekly_breakdown', pd
        .DataFrame())
    daily_breakdown: pd.DataFrame = report_data.get('daily_breakdown', pd.
        DataFrame())
    raw_data: pd.DataFrame = report_data.get('raw_data', pd.DataFrame())
    output_dir = Path(output_dir) if output_dir else REPORTS_DIR / 'monthly'
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f'Monthly_Report_{summary.period_label}.xlsx'
    filepath = output_dir / filename
    wb = Workbook()
    _write_summary_sheet(wb, summary, 'Monthly Report')
    if not user_data.empty:
        _write_dataframe_sheet(wb, user_data, 'User Summary', is_summary=True)
    if not weekly_breakdown.empty:
        _write_dataframe_sheet(wb, weekly_breakdown, 'Weekly Breakdown')
    if not daily_breakdown.empty:
        _write_dataframe_sheet(wb, daily_breakdown, 'Daily Breakdown')
    if not raw_data.empty:
        display_cols = _select_display_columns(raw_data)
        _write_dataframe_sheet(wb, raw_data[display_cols], 'Raw Data')
    validation = report_data.get('validation')
    if validation:
        _write_validation_sheet(wb, validation)
    if len(wb.sheetnames) > 1 and 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    return filepath


def _write_summary_sheet(wb: Workbook, summary: ReportSummary, title: str
    ) ->None:
    """Write the summary overview sheet with normal/error and workflow transition metrics."""
    ws = (wb.active if wb.active and wb.active.title == 'Sheet' else wb.
        create_sheet())
    ws.title = 'Summary'
    ws['A1'] = title
    ws['A1'].font = TITLE_FONT
    ws['A2'] = f'Period: {summary.period_label}'
    ws['A2'].font = SUBTITLE_FONT
    ws['A3'] = f"Generated: {summary.generated_at.strftime('%Y-%m-%d %H:%M')}"
    ws['A3'].font = Font(name='Calibri', size=9, italic=True, color='808080')
    row = 5
    metrics = [('Report Type', summary.report_type.title()), (
        'Period Start', summary.period_start), ('Period End', summary.
        period_end), ('Completed Normal Tasks', str(summary.
        total_completed_normal_count)), ('Completed Normal Duration',
        summary.total_completed_normal_duration_display), (
        'Completed Error Tasks', str(summary.total_completed_error_count)),
        ('Completed Error Duration', summary.
        total_completed_error_duration_display), ('Total Completed Tasks',
        str(summary.total_completed_count)), ('Total Completed Duration',
        summary.total_completed_duration_display), ('Total Submitted Tasks',
        str(summary.total_submitted_count)), ('Total Submitted Duration',
        summary.total_submitted_duration_display), ('Leader Passed Tasks',
        str(summary.total_leader_passed_count)), ('Leader Passed Duration',
        summary.total_leader_passed_duration_display), (
        'Leader Returned Tasks', str(summary.total_leader_returned_count)),
        ('Leader Returned Duration', summary.
        total_leader_returned_duration_display), ('Auditor Passed Tasks',
        str(summary.total_auditor_passed_count)), (
        'Auditor Passed Duration', summary.
        total_auditor_passed_duration_display), ('Auditor Returned Tasks',
        str(summary.total_auditor_returned_count)), (
        'Auditor Returned Duration', summary.
        total_auditor_returned_duration_display), ('Users', str(len(summary
        .user_summaries)))]
    for label, value in metrics:
        ws.cell(row=row, column=1, value=label).font = Font(name='Calibri',
            bold=True, size=10)
        ws.cell(row=row, column=2, value=value).font = DATA_FONT
        row += 1
    if summary.user_summaries:
        row += 1
        ws.cell(row=row, column=1, value='User Breakdown').font = SUBTITLE_FONT
        row += 1
        headers = ['User', 'Normal Count', 'Normal Duration', 'Error Count',
            'Error Duration', 'Total Completed Count',
            'Total Completed Duration', 'Submitted Count',
            'Submitted Duration', 'Leader Passed Count',
            'Leader Passed Duration', 'Leader Returned Count',
            'Leader Returned Duration', 'Auditor Passed Count',
            'Auditor Passed Duration', 'Auditor Returned Count',
            'Auditor Returned Duration', 'Contribution %']
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col_idx, value=header)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = HEADER_ALIGNMENT
            cell.border = THIN_BORDER
        row += 1
        for user in summary.user_summaries:
            ws.cell(row=row, column=1, value=user.user_name).font = DATA_FONT
            ws.cell(row=row, column=2, value=user.completed_normal_count
                ).font = DATA_FONT
            ws.cell(row=row, column=3, value=user.
                completed_normal_duration_display).font = DATA_FONT
            ws.cell(row=row, column=4, value=user.completed_error_count
                ).font = DATA_FONT
            ws.cell(row=row, column=5, value=user.
                completed_error_duration_display).font = DATA_FONT
            ws.cell(row=row, column=6, value=user.total_completed_count
                ).font = DATA_FONT
            ws.cell(row=row, column=7, value=user.
                total_completed_duration_display).font = DATA_FONT
            ws.cell(row=row, column=8, value=user.submitted_count
                ).font = DATA_FONT
            ws.cell(row=row, column=9, value=user.submitted_duration_display
                ).font = DATA_FONT
            ws.cell(row=row, column=10, value=user.leader_passed_count
                ).font = DATA_FONT
            ws.cell(row=row, column=11, value=user.
                leader_passed_duration_display).font = DATA_FONT
            ws.cell(row=row, column=12, value=user.leader_returned_count
                ).font = DATA_FONT
            ws.cell(row=row, column=13, value=user.
                leader_returned_duration_display).font = DATA_FONT
            ws.cell(row=row, column=14, value=user.auditor_passed_count
                ).font = DATA_FONT
            ws.cell(row=row, column=15, value=user.
                auditor_passed_duration_display).font = DATA_FONT
            ws.cell(row=row, column=16, value=user.auditor_returned_count
                ).font = DATA_FONT
            ws.cell(row=row, column=17, value=user.
                auditor_returned_duration_display).font = DATA_FONT
            ws.cell(row=row, column=18, value=
                f'{user.percentage_contribution}%').font = DATA_FONT
            for col_idx in range(1, 19):
                ws.cell(row=row, column=col_idx).border = THIN_BORDER
            row += 1
        ws.cell(row=row, column=1, value='TOTAL').font = TOTAL_FONT
        ws.cell(row=row, column=2, value=summary.total_completed_normal_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=3, value=summary.
            total_completed_normal_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=4, value=summary.total_completed_error_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=5, value=summary.
            total_completed_error_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=6, value=summary.total_completed_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=7, value=summary.
            total_completed_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=8, value=summary.total_submitted_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=9, value=summary.
            total_submitted_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=10, value=summary.total_leader_passed_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=11, value=summary.
            total_leader_passed_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=12, value=summary.total_leader_returned_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=13, value=summary.
            total_leader_returned_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=14, value=summary.total_auditor_passed_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=15, value=summary.
            total_auditor_passed_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=16, value=summary.total_auditor_returned_count
            ).font = TOTAL_FONT
        ws.cell(row=row, column=17, value=summary.
            total_auditor_returned_duration_display).font = TOTAL_FONT
        ws.cell(row=row, column=18, value='100%').font = TOTAL_FONT
        for col_idx in range(1, 19):
            cell = ws.cell(row=row, column=col_idx)
            cell.fill = TOTAL_FILL
            cell.border = THIN_BORDER
    _auto_width(ws)


def _write_dataframe_sheet(wb: Workbook, df: pd.DataFrame, sheet_name: str,
    is_summary: bool=False) ->None:
    """Write a DataFrame to a new worksheet with formatting."""
    ws = wb.create_sheet(title=sheet_name)
    for col_idx, col_name in enumerate(df.columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=str(col_name))
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    for row_idx, (_, row) in enumerate(df.iterrows(), 2):
        for col_idx, value in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=_clean_value(
                value))
            cell.font = DATA_FONT
            cell.border = THIN_BORDER
            if isinstance(value, (int, float)):
                cell.alignment = NUMBER_ALIGNMENT
    _auto_width(ws)
    ws.freeze_panes = 'A2'


def _write_validation_sheet(wb: Workbook, validation: dict[str, Any]) ->None:
    """Write validation results to a sheet."""
    ws = wb.create_sheet(title='Validation')
    ws['A1'] = 'Data Validation Report'
    ws['A1'].font = TITLE_FONT
    row = 3
    ws.cell(row=row, column=1, value='Overall Status').font = Font(name=
        'Calibri', bold=True)
    is_valid = validation.get('overall_valid', True)
    status_cell = ws.cell(row=row, column=2, value='PASSED' if is_valid else
        'FAILED')
    status_cell.font = Font(name='Calibri', bold=True, color='006100' if
        is_valid else '9C0006')
    status_cell.fill = PatternFill(start_color='C6EFCE' if is_valid else
        'FFC7CE', end_color='C6EFCE' if is_valid else 'FFC7CE', fill_type=
        'solid')
    row += 2
    errors = validation.get('errors', [])
    if errors:
        ws.cell(row=row, column=1, value='Errors').font = Font(name=
            'Calibri', bold=True, color='9C0006')
        row += 1
        for error in errors:
            cell = ws.cell(row=row, column=1, value=f'❌ {error}')
            cell.font = DATA_FONT
            cell.fill = ERROR_FILL
            row += 1
    warnings = validation.get('warnings', [])
    if warnings:
        row += 1
        ws.cell(row=row, column=1, value='Warnings').font = Font(name=
            'Calibri', bold=True, color='9C5700')
        row += 1
        for warning in warnings:
            cell = ws.cell(row=row, column=1, value=f'⚠️ {warning}')
            cell.font = DATA_FONT
            cell.fill = WARNING_FILL
            row += 1
    details = validation.get('details', {})
    if details:
        row += 1
        ws.cell(row=row, column=1, value='Details').font = SUBTITLE_FONT
        row += 1
        for key, value in _flatten_dict(details).items():
            ws.cell(row=row, column=1, value=str(key)).font = Font(name=
                'Calibri', bold=True, size=9)
            ws.cell(row=row, column=2, value=str(value)).font = Font(name=
                'Calibri', size=9)
            row += 1
    _auto_width(ws)


def _auto_width(ws: Any) ->None:
    """Auto-adjust column widths based on content."""
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                cell_len = len(str(cell.value or ''))
                max_length = max(max_length, cell_len)
            except Exception:
                pass
        adjusted = min(max_length + 3, 50)
        ws.column_dimensions[col_letter].width = adjusted


def _clean_value(value: Any) ->Any:
    """Clean a value for Excel output."""
    if pd.isna(value):
        return ''
    if isinstance(value, pd.Timestamp):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return value


def _select_display_columns(df: pd.DataFrame) ->list[str]:
    """Select columns suitable for display in a report sheet."""
    exclude = {'record_id', 'source_extracted_at', 'extra_fields', 'raw_data'}
    return [col for col in df.columns if col not in exclude]


def _flatten_dict(d: dict, parent_key: str='', sep: str='.') ->dict[str, str]:
    """Flatten a nested dictionary for display."""
    items: list[tuple[str, str]] = []
    for k, v in d.items():
        new_key = f'{parent_key}{sep}{k}' if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        elif isinstance(v, list):
            items.append((new_key, ', '.join(str(x) for x in v)))
        else:
            items.append((new_key, str(v)))
    return dict(items)
