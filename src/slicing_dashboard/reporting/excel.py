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
from slicing_dashboard.extraction.parsers import format_duration
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


def write_individual_report(report_data: dict[str, Any], output_dir: (str | Path | None) = None) -> Path:
    """Write a comprehensive individual performance report workbook.

    Sheets:
    1. Individual Summary — High-level performance metrics, rates, and KPIs
    2. Monthly Daily Distribution — Calendar day-by-day status + return breakdown
    3. Today Activity — Operational status, first-time vs rework, and time analysis
    4. Today Video Detail — Video-level audit records for today's tasks
    5. Data Helper — Underlying task records for validation
    """
    individual = report_data.get("individual", "User")
    target_date = report_data.get("target_date", datetime.now().strftime("%Y-%m-%d"))
    target_month = report_data.get("target_month", target_date[:7])

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = REPORTS_DIR / "individual" / individual / target_month
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"Individual_Report_{individual}_{target_month}.xlsx"
    filepath = out_dir / filename

    wb = Workbook()

    _write_individual_summary_sheet(wb, report_data)
    _write_individual_monthly_daily_sheet(wb, report_data)
    _write_individual_today_sheet(wb, report_data)
    _write_individual_video_detail_sheet(wb, report_data)
    _write_individual_helper_sheet(wb, report_data)

    if len(wb.sheetnames) > 1 and "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    wb.save(filepath)
    return filepath


def _write_individual_summary_sheet(wb: Workbook, report_data: dict[str, Any]) -> None:
    """Write Sheet 1: Individual Summary with KPIs and metrics."""
    ws = wb.active if wb.active and wb.active.title == "Sheet" else wb.create_sheet()
    ws.title = "Individual Summary"

    ind = report_data.get("individual", "Individual")
    s = report_data.get("summary", {})
    t_month = report_data.get("target_month", "")
    t_date = report_data.get("target_date", "")
    aliases = report_data.get("aliases", [])

    ws["A1"] = f"Individual Performance Summary: {ind}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Month: {t_month}  |  Target Date: {t_date}  |  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws["A2"].font = SUBTITLE_FONT
    ws["A3"] = f"Account Aliases Included: {', '.join(aliases)}"
    ws["A3"].font = Font(name="Calibri", size=9, italic=True, color="595959")

    # KPI Summary Cards Block (Rows 5-8)
    kpis = [
        ("Total Completed Work", f"{s.get('total_completed_count', 0)} tasks ({s.get('total_completed_duration_display', '00:00:00')})"),
        ("Total Pending Work", f"{s.get('total_pending_count', 0)} tasks ({s.get('total_pending_duration_display', '00:00:00')})"),
        ("Total Assigned Work", f"{s.get('total_assigned_count', 0)} tasks ({s.get('total_assigned_duration_display', '00:00:00')})"),
        ("Total Rework Work", f"{s.get('total_rework_count', 0)} tasks ({s.get('total_rework_duration_display', '00:00:00')})"),
        ("Completion Rate", f"{s.get('completion_rate_pct', 0.0)}%"),
        ("Quality Error Rate", f"{s.get('error_rate_pct', 0.0)}%"),
        ("Rework Rate", f"{s.get('rework_rate_pct', 0.0)}%"),
        ("Avg Duration / Completed Task", s.get("avg_completed_duration_display", "00:00:00")),
    ]

    ws.cell(row=5, column=1, value="High-Level Performance KPIs").font = SUBTITLE_FONT
    row = 6
    for i in range(0, len(kpis), 2):
        label1, val1 = kpis[i]
        ws.cell(row=row, column=1, value=label1).font = Font(name="Calibri", bold=True, size=10)
        ws.cell(row=row, column=2, value=val1).font = DATA_FONT

        if i + 1 < len(kpis):
            label2, val2 = kpis[i + 1]
            ws.cell(row=row, column=3, value=label2).font = Font(name="Calibri", bold=True, size=10)
            ws.cell(row=row, column=4, value=val2).font = DATA_FONT
        row += 1

    # Detailed Breakdown Table (Row 11+)
    row = 11
    ws.cell(row=row, column=1, value="Detailed Work Breakdown").font = SUBTITLE_FONT
    row += 1

    headers = ["Metric Category", "Task Count", "Duration (Seconds)", "Duration (HH:MM:SS)", "Hours (Decimal)"]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    row += 1

    table_data = [
        ("Completed Normal Tasks", s.get("completed_normal_count", 0), s.get("completed_normal_duration_seconds", 0.0), s.get("completed_normal_duration_display", "00:00:00")),
        ("Completed Error Tasks", s.get("completed_error_count", 0), s.get("completed_error_duration_seconds", 0.0), s.get("completed_error_duration_display", "00:00:00")),
        ("Total Completed Tasks", s.get("total_completed_count", 0), s.get("total_completed_duration_seconds", 0.0), s.get("total_completed_duration_display", "00:00:00")),
        ("Pending Leader Review", s.get("pending_leader_count", 0), s.get("pending_leader_duration_seconds", 0.0), s.get("pending_leader_duration_display", "00:00:00")),
        ("Pending Auditor Review", s.get("pending_auditor_count", 0), s.get("pending_auditor_duration_seconds", 0.0), s.get("pending_auditor_duration_display", "00:00:00")),
        ("Pending Admin Review", s.get("pending_admin_count", 0), s.get("pending_admin_duration_seconds", 0.0), s.get("pending_admin_duration_display", "00:00:00")),
        ("Total Pending Review", s.get("total_pending_count", 0), s.get("total_pending_duration_seconds", 0.0), s.get("total_pending_duration_display", "00:00:00")),
        ("Total Assigned Tasks", s.get("total_assigned_count", 0), s.get("total_assigned_duration_seconds", 0.0), s.get("total_assigned_duration_display", "00:00:00")),
        ("Total Rework Tasks", s.get("total_rework_count", 0), s.get("total_rework_duration_seconds", 0.0), s.get("total_rework_duration_display", "00:00:00")),
        ("Total Submitted Tasks (Transitions)", s.get("submitted_count", 0), s.get("submitted_duration_seconds", 0.0), s.get("submitted_duration_display", "00:00:00")),
        ("Leader Passed Tasks (Transitions)", s.get("leader_passed_count", 0), s.get("leader_passed_duration_seconds", 0.0), s.get("leader_passed_duration_display", "00:00:00")),
        ("Auditor Passed Tasks (Transitions)", s.get("auditor_passed_count", 0), s.get("auditor_passed_duration_seconds", 0.0), s.get("auditor_passed_duration_display", "00:00:00")),
    ]

    for label, count_val, dur_sec, dur_disp in table_data:
        is_highlight = label in ("Total Completed Tasks", "Total Pending Review")
        c1 = ws.cell(row=row, column=1, value=label)
        c2 = ws.cell(row=row, column=2, value=count_val)
        c3 = ws.cell(row=row, column=3, value=dur_sec)
        c4 = ws.cell(row=row, column=4, value=dur_disp)
        c5 = ws.cell(row=row, column=5, value=round(dur_sec / 3600.0, 2))

        for c in [c1, c2, c3, c4, c5]:
            c.border = THIN_BORDER
            c.font = TOTAL_FONT if is_highlight else DATA_FONT
            if is_highlight:
                c.fill = TOTAL_FILL

        c1.alignment = DATA_ALIGNMENT
        c2.alignment = NUMBER_ALIGNMENT
        c3.alignment = NUMBER_ALIGNMENT
        c4.alignment = Alignment(horizontal="center", vertical="center")
        c5.alignment = NUMBER_ALIGNMENT
        row += 1

    _auto_width(ws)


def _write_individual_monthly_daily_sheet(wb: Workbook, report_data: dict[str, Any]) -> None:
    """Write Sheet 2: Monthly Daily Distribution with day-by-day status and returns."""
    ws = wb.create_sheet(title="Monthly Daily Distribution")

    ind = report_data.get("individual", "Individual")
    t_month = report_data.get("target_month", "")
    df: pd.DataFrame = report_data.get("monthly_daily", pd.DataFrame())

    ws["A1"] = f"Monthly Daily Distribution — {t_month}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Individual: {ind}"
    ws["A2"].font = SUBTITLE_FONT

    if df.empty:
        ws["A4"] = "No daily records for this month."
        return

    headers = list(df.columns)
    start_row = 4
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER

    cur_row = start_row + 1
    for _, row in df.iterrows():
        for c_idx, h in enumerate(headers, 1):
            val = row[h]
            cell = ws.cell(row=cur_row, column=c_idx, value=val)
            cell.font = DATA_FONT
            cell.border = THIN_BORDER

            if c_idx == 1:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = NUMBER_ALIGNMENT
                if "Hours" in h:
                    cell.number_format = "0.00"
                else:
                    cell.number_format = "#,##0"
        cur_row += 1

    # Bottom Total Summary Row with SUM formulas
    tot_cell = ws.cell(row=cur_row, column=1, value="Total")
    tot_cell.font = TOTAL_FONT
    tot_cell.fill = TOTAL_FILL
    tot_cell.alignment = Alignment(horizontal="center", vertical="center")
    tot_cell.border = THIN_BORDER

    for c_idx in range(2, len(headers) + 1):
        col_letter = ws.cell(row=start_row, column=c_idx).column_letter
        c = ws.cell(row=cur_row, column=c_idx)
        c.value = f"=SUM({col_letter}{start_row + 1}:{col_letter}{cur_row - 1})"
        c.font = TOTAL_FONT
        c.fill = TOTAL_FILL
        c.border = THIN_BORDER
        c.alignment = NUMBER_ALIGNMENT
        h_name = headers[c_idx - 1]
        c.number_format = "0.00" if "Hours" in h_name else "#,##0"

    _auto_width(ws)


def _write_individual_today_sheet(wb: Workbook, report_data: dict[str, Any]) -> None:
    """Write Sheet 3: Today's Activity (Operational Status, First-Time vs Rework, Time Analysis)."""
    ws = wb.create_sheet(title="Today Activity")

    ind = report_data.get("individual", "Individual")
    ts = report_data.get("today_summary", {})
    t_date = ts.get("target_date", report_data.get("target_date", ""))

    ws["A1"] = f"Today's Operational Activity & Time Analysis: {t_date}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Individual: {ind}"
    ws["A2"].font = SUBTITLE_FONT

    row = 4

    # Section 1: Operational Status Today
    ws.cell(row=row, column=1, value="1. Operational Status Today").font = SUBTITLE_FONT
    row += 1

    op_headers = ["Operational Category", "Task Count", "Duration (Hours)"]
    for c_idx, h in enumerate(op_headers, 1):
        cell = ws.cell(row=row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    row += 1

    op_items = [
        ("Assigned Today", ts.get("assigned_today_count", 0), ts.get("assigned_today_hours", 0.0)),
        ("Rework / Returned Today", ts.get("rework_returned_today_count", 0), ts.get("rework_returned_today_hours", 0.0)),
        ("Pending Review Today", ts.get("pending_review_today_count", 0), ts.get("pending_review_today_hours", 0.0)),
        ("Completed Today", ts.get("completed_today_count", 0), ts.get("completed_today_hours", 0.0)),
    ]

    for label, count_val, hrs in op_items:
        c1 = ws.cell(row=row, column=1, value=label)
        c2 = ws.cell(row=row, column=2, value=count_val)
        c3 = ws.cell(row=row, column=3, value=hrs)
        for c in [c1, c2, c3]:
            c.font = DATA_FONT
            c.border = THIN_BORDER
        c1.alignment = DATA_ALIGNMENT
        c2.alignment = NUMBER_ALIGNMENT
        c3.alignment = NUMBER_ALIGNMENT
        c3.number_format = "0.00"
        row += 1

    row += 1

    # Section 2: First-Time vs Rework vs Partial Batches
    ws.cell(row=row, column=1, value="2. Work Type Classification (First-Time vs Rework)").font = SUBTITLE_FONT
    row += 1

    work_headers = ["Work Classification", "Task Count", "Duration (Hours)", "% of Today's Work"]
    for c_idx, h in enumerate(work_headers, 1):
        cell = ws.cell(row=row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    row += 1

    tot_tasks = ts.get("first_time_count", 0) + ts.get("rework_count", 0)
    ft_pct = round(ts.get("first_time_count", 0) / tot_tasks * 100, 1) if tot_tasks > 0 else 0.0
    rew_pct = round(ts.get("rework_count", 0) / tot_tasks * 100, 1) if tot_tasks > 0 else 0.0
    part_pct = round(ts.get("partial_batch_count", 0) / tot_tasks * 100, 1) if tot_tasks > 0 else 0.0

    work_items = [
        ("First-Time Work (New Videos)", ts.get("first_time_count", 0), ts.get("first_time_hours", 0.0), f"{ft_pct}%"),
        ("Rework / Reopened Videos", ts.get("rework_count", 0), ts.get("rework_hours", 0.0), f"{rew_pct}%"),
        ("Partially Completed Batches", ts.get("partial_batch_count", 0), ts.get("partial_batch_hours", 0.0), f"{part_pct}%"),
    ]

    for label, count_val, hrs, pct_val in work_items:
        c1 = ws.cell(row=row, column=1, value=label)
        c2 = ws.cell(row=row, column=2, value=count_val)
        c3 = ws.cell(row=row, column=3, value=hrs)
        c4 = ws.cell(row=row, column=4, value=pct_val)
        for c in [c1, c2, c3, c4]:
            c.font = DATA_FONT
            c.border = THIN_BORDER
        c1.alignment = DATA_ALIGNMENT
        c2.alignment = NUMBER_ALIGNMENT
        c3.alignment = NUMBER_ALIGNMENT
        c4.alignment = Alignment(horizontal="center", vertical="center")
        c3.number_format = "0.00"
        row += 1

    row += 1

    # Section 3: Time Analysis
    ws.cell(row=row, column=1, value="3. Today's Time Analysis").font = SUBTITLE_FONT
    row += 1

    time_headers = ["Time Metric", "Value (HH:MM:SS)", "Value (Hours Decimal)"]
    for c_idx, h in enumerate(time_headers, 1):
        cell = ws.cell(row=row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    row += 1

    time_metrics = [
        ("Total Time Spent Today", ts.get("total_time_spent_display", "00:00:00"), ts.get("total_time_spent_hours", 0.0)),
        ("Avg Time per Completed Video", ts.get("avg_time_per_completed_display", "00:00:00"), "-"),
        ("Avg Time for First-Time Work", ts.get("avg_time_first_time_display", "00:00:00"), "-"),
        ("Avg Time for Rework", ts.get("avg_time_rework_display", "00:00:00"), "-"),
        ("Time Spent on Partially Completed Batches", format_duration(ts.get("partial_batch_hours", 0.0) * 3600), ts.get("partial_batch_hours", 0.0)),
    ]

    for label, val_disp, val_dec in time_metrics:
        c1 = ws.cell(row=row, column=1, value=label)
        c2 = ws.cell(row=row, column=2, value=val_disp)
        c3 = ws.cell(row=row, column=3, value=val_dec)
        for c in [c1, c2, c3]:
            c.font = DATA_FONT
            c.border = THIN_BORDER
        c1.alignment = DATA_ALIGNMENT
        c2.alignment = Alignment(horizontal="center", vertical="center")
        c3.alignment = NUMBER_ALIGNMENT
        if isinstance(val_dec, (int, float)):
            c3.number_format = "0.00"
        row += 1

    _auto_width(ws)


def _write_individual_video_detail_sheet(wb: Workbook, report_data: dict[str, Any]) -> None:
    """Write Sheet 4: Today's Video Detail audit records."""
    ws = wb.create_sheet(title="Today Video Detail")

    ind = report_data.get("individual", "Individual")
    t_date = report_data.get("target_date", "")
    df: pd.DataFrame = report_data.get("today_video_detail", pd.DataFrame())

    ws["A1"] = f"Video-Level Task Audit Records: {t_date}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Individual: {ind}"
    ws["A2"].font = SUBTITLE_FONT

    if df.empty:
        ws["A4"] = "No video records active or completed for this date."
        return

    headers = list(df.columns)
    start_row = 4
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER

    cur_row = start_row + 1
    for _, row in df.iterrows():
        for c_idx, h in enumerate(headers, 1):
            val = row[h]
            cell = ws.cell(row=cur_row, column=c_idx, value=_clean_value(val))
            cell.font = DATA_FONT
            cell.border = THIN_BORDER

            if h in ("Video ID", "Task ID", "Account ID", "Batch ID"):
                cell.alignment = DATA_ALIGNMENT
            elif h in ("Is First-Time", "Is Rework", "Is Partial Batch", "Returned By", "Time Flag", "Previous Worked Date"):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif h == "Work Status":
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = NUMBER_ALIGNMENT
                if "Hours" in h:
                    cell.number_format = "0.000"
                elif "Seconds" in h:
                    cell.number_format = "#,##0"
        cur_row += 1

    _auto_width(ws)


def _write_individual_helper_sheet(wb: Workbook, report_data: dict[str, Any]) -> None:
    """Write Sheet 5: Data Helper underlying master data."""
    ws = wb.create_sheet(title="Data Helper")

    ind = report_data.get("individual", "Individual")
    tasks_df: pd.DataFrame = report_data.get("data_helper_tasks", pd.DataFrame())

    ws["A1"] = f"Underlying Slicing Master Tasks: {ind}"
    ws["A1"].font = TITLE_FONT

    if tasks_df.empty:
        ws["A3"] = "No master task records found for this individual."
        return

    headers = list(tasks_df.columns)
    start_row = 3
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=c_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER

    cur_row = start_row + 1
    for _, row in tasks_df.iterrows():
        for c_idx, h in enumerate(headers, 1):
            val = row[h]
            cell = ws.cell(row=cur_row, column=c_idx, value=_clean_value(val))
            cell.font = DATA_FONT
            cell.border = THIN_BORDER
        cur_row += 1

    _auto_width(ws)

