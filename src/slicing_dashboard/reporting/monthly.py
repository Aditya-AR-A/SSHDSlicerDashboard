"""
Monthly report generation.

Generates monthly completion summaries:
- Total completed tasks and duration for the month
- User-wise totals
- Weekly breakdown within the month
- Daily trend
- Month-over-month comparison (when available)
"""
from __future__ import annotations
from datetime import datetime
from typing import Any
import pandas as pd
from slicing_dashboard.extraction.parsers import format_duration
from slicing_dashboard.models.schemas import ReportSummary, UserSummary


def generate_monthly_report(df: pd.DataFrame, month: (str | None)=None,
    user_column: str='user_name', duration_column: str='duration_seconds'
    ) ->dict[str, Any]:
    """Generate a monthly report from processed data."""
    if month is None:
        month = datetime.now().strftime('%Y-%m')
    if 'completed_month' not in df.columns:
        return _empty_monthly_report(month)
    report_df = df[(df['is_completed'] == True) & (df['completed_date'] >=
        '2026-07-07')].copy()
    monthly_df = report_df[report_df['completed_month'] == month].copy()
    from slicing_dashboard.config import PROCESSED_DIR
    transitions_path = PROCESSED_DIR / 'transitions_master.csv'
    if transitions_path.exists():
        transitions_df = pd.read_csv(transitions_path)
        transitions_df['month'] = pd.to_datetime(transitions_df['date']
            ).dt.strftime('%Y-%m')
        monthly_transitions = transitions_df[transitions_df['month'] == month]
    else:
        monthly_transitions = pd.DataFrame(columns=['task_id', 'user_name',
            'duration_seconds', 'date', 'type', 'month'])
    if monthly_df.empty and monthly_transitions.empty:
        return _empty_monthly_report(month)
    all_dates = []
    if not monthly_df.empty:
        all_dates.extend(monthly_df['completed_date'].dropna().unique())
    if not monthly_transitions.empty:
        all_dates.extend(monthly_transitions['date'].dropna().astype(str).
            unique())
    period_start = min(all_dates) if all_dates else f'{month}-01'
    period_end = max(all_dates) if all_dates else f'{month}-28'
    users_from_df = set(monthly_df[user_column].dropna().unique()
        ) if not monthly_df.empty else set()
    users_from_trans = set(monthly_transitions['user_name'].dropna().unique()
        ) if not monthly_transitions.empty else set()
    users_list = sorted(list(users_from_df.union(users_from_trans)))
    user_rows = []
    for user in users_list:
        user_df = monthly_df[monthly_df[user_column] == user
            ] if not monthly_df.empty else pd.DataFrame()
        user_trans = monthly_transitions[monthly_transitions['user_name'] ==
            user] if not monthly_transitions.empty else pd.DataFrame()
        if not user_df.empty:
            normal_tasks = user_df[user_df['completion_type'] == 'normal']
            normal_count = len(normal_tasks)
            normal_dur = normal_tasks[duration_column].sum()
            error_tasks = user_df[user_df['completion_type'] == 'error']
            error_count = len(error_tasks)
            error_dur = error_tasks[duration_column].sum()
        else:
            normal_count, normal_dur = 0, 0.0
            error_count, error_dur = 0, 0.0
        total_count = normal_count + error_count
        total_dur = normal_dur + error_dur
        sub_tasks = user_trans[user_trans['type'] == 'submitted']
        sub_count = len(sub_tasks)
        sub_dur = sub_tasks['duration_seconds'].sum()
        lp_tasks = user_trans[user_trans['type'] == 'leader_passed']
        lp_count = len(lp_tasks)
        lp_dur = lp_tasks['duration_seconds'].sum()
        lr_tasks = user_trans[user_trans['type'] == 'leader_returned']
        lr_count = len(lr_tasks)
        lr_dur = lr_tasks['duration_seconds'].sum()
        ap_tasks = user_trans[user_trans['type'] == 'auditor_passed']
        ap_count = len(ap_tasks)
        ap_dur = ap_tasks['duration_seconds'].sum()
        ar_tasks = user_trans[user_trans['type'] == 'auditor_returned']
        ar_count = len(ar_tasks)
        ar_dur = ar_tasks['duration_seconds'].sum()
        user_rows.append({user_column: user, 'completed_normal_count':
            normal_count, 'completed_normal_duration_seconds': normal_dur,
            'completed_normal_duration_display': format_duration(normal_dur
            ), 'completed_error_count': error_count,
            'completed_error_duration_seconds': error_dur,
            'completed_error_duration_display': format_duration(error_dur),
            'total_completed_count': total_count,
            'total_completed_duration_seconds': total_dur,
            'total_completed_duration_display': format_duration(total_dur),
            'submitted_count': sub_count, 'submitted_duration_seconds':
            sub_dur, 'submitted_duration_display': format_duration(sub_dur),
            'leader_passed_count': lp_count,
            'leader_passed_duration_seconds': lp_dur,
            'leader_passed_duration_display': format_duration(lp_dur),
            'leader_returned_count': lr_count,
            'leader_returned_duration_seconds': lr_dur,
            'leader_returned_duration_display': format_duration(lr_dur),
            'auditor_passed_count': ap_count,
            'auditor_passed_duration_seconds': ap_dur,
            'auditor_passed_duration_display': format_duration(ap_dur),
            'auditor_returned_count': ar_count,
            'auditor_returned_duration_seconds': ar_dur,
            'auditor_returned_duration_display': format_duration(ar_dur)})
    user_agg = pd.DataFrame(user_rows) if user_rows else pd.DataFrame(columns
        =[user_column, 'completed_normal_count',
        'completed_normal_duration_seconds',
        'completed_normal_duration_display', 'completed_error_count',
        'completed_error_duration_seconds',
        'completed_error_duration_display', 'total_completed_count',
        'total_completed_duration_seconds',
        'total_completed_duration_display', 'submitted_count',
        'submitted_duration_seconds', 'submitted_duration_display',
        'leader_passed_count', 'leader_passed_duration_seconds',
        'leader_passed_duration_display', 'leader_returned_count',
        'leader_returned_duration_seconds',
        'leader_returned_duration_display', 'auditor_passed_count',
        'auditor_passed_duration_seconds',
        'auditor_passed_duration_display', 'auditor_returned_count',
        'auditor_returned_duration_seconds',
        'auditor_returned_duration_display'])
    grand_total_seconds = user_agg['total_completed_duration_seconds'].sum()
    if grand_total_seconds > 0:
        user_agg['percentage_contribution'] = (user_agg[
            'total_completed_duration_seconds'] / grand_total_seconds * 100
            ).round(1)
    else:
        user_agg['percentage_contribution'] = 0.0
    user_agg = user_agg.sort_values('total_completed_duration_seconds',
        ascending=False)
    weekly_breakdown = pd.DataFrame()
    if 'completed_week' in monthly_df.columns:
        weekly_breakdown = monthly_df.groupby(['completed_week', user_column]
            ).agg(total_tasks=(duration_column, 'count'), duration_seconds=
            (duration_column, 'sum')).reset_index()
        weekly_breakdown['duration_display'] = weekly_breakdown[
            'duration_seconds'].apply(format_duration)
    weekly_totals = pd.DataFrame()
    if 'completed_week' in monthly_df.columns:
        weekly_totals = monthly_df.groupby('completed_week').agg(total_tasks
            =(duration_column, 'count'), duration_seconds=(duration_column,
            'sum')).reset_index()
        weekly_totals['duration_display'] = weekly_totals['duration_seconds'
            ].apply(format_duration)
    daily_breakdown = monthly_df.groupby(['completed_date', user_column]).agg(
        total_tasks=(duration_column, 'count'), duration_seconds=(
        duration_column, 'sum')).reset_index()
    daily_breakdown['duration_display'] = daily_breakdown['duration_seconds'
        ].apply(format_duration)
    daily_totals = monthly_df.groupby('completed_date').agg(total_tasks=(
        duration_column, 'count'), duration_seconds=(duration_column, 'sum')
        ).reset_index()
    daily_totals['duration_display'] = daily_totals['duration_seconds'].apply(
        format_duration)
    prev_month_data = _get_previous_month_summary(report_df, month,
        user_column, duration_column)
    user_summaries = [UserSummary(user_name=row[user_column],
        completed_normal_count=int(row['completed_normal_count']),
        completed_normal_duration_seconds=row[
        'completed_normal_duration_seconds'],
        completed_normal_duration_display=row[
        'completed_normal_duration_display'], completed_error_count=int(row
        ['completed_error_count']), completed_error_duration_seconds=row[
        'completed_error_duration_seconds'],
        completed_error_duration_display=row[
        'completed_error_duration_display'], total_completed_count=int(row[
        'total_completed_count']), total_completed_duration_seconds=row[
        'total_completed_duration_seconds'],
        total_completed_duration_display=row[
        'total_completed_duration_display'], submitted_count=int(row[
        'submitted_count']), submitted_duration_seconds=row[
        'submitted_duration_seconds'], submitted_duration_display=row[
        'submitted_duration_display'], leader_passed_count=int(row[
        'leader_passed_count']), leader_passed_duration_seconds=row[
        'leader_passed_duration_seconds'], leader_passed_duration_display=
        row['leader_passed_duration_display'], leader_returned_count=int(
        row['leader_returned_count']), leader_returned_duration_seconds=row
        ['leader_returned_duration_seconds'],
        leader_returned_duration_display=row[
        'leader_returned_duration_display'], auditor_passed_count=int(row[
        'auditor_passed_count']), auditor_passed_duration_seconds=row[
        'auditor_passed_duration_seconds'], auditor_passed_duration_display
        =row['auditor_passed_duration_display'], auditor_returned_count=int
        (row['auditor_returned_count']), auditor_returned_duration_seconds=
        row['auditor_returned_duration_seconds'],
        auditor_returned_duration_display=row[
        'auditor_returned_duration_display'], percentage_contribution=row[
        'percentage_contribution']) for _, row in user_agg.iterrows()]
    summary = ReportSummary(report_type='monthly', period_label=month,
        period_start=str(period_start), period_end=str(period_end),
        total_completed_normal_count=int(user_agg['completed_normal_count']
        .sum()), total_completed_normal_duration_seconds=user_agg[
        'completed_normal_duration_seconds'].sum(),
        total_completed_normal_duration_display=format_duration(user_agg[
        'completed_normal_duration_seconds'].sum()),
        total_completed_error_count=int(user_agg['completed_error_count'].
        sum()), total_completed_error_duration_seconds=user_agg[
        'completed_error_duration_seconds'].sum(),
        total_completed_error_duration_display=format_duration(user_agg[
        'completed_error_duration_seconds'].sum()), total_completed_count=
        int(user_agg['total_completed_count'].sum()),
        total_completed_duration_seconds=grand_total_seconds,
        total_completed_duration_display=format_duration(
        grand_total_seconds), total_submitted_count=int(user_agg[
        'submitted_count'].sum()), total_submitted_duration_seconds=
        user_agg['submitted_duration_seconds'].sum(),
        total_submitted_duration_display=format_duration(user_agg[
        'submitted_duration_seconds'].sum()), total_leader_passed_count=int
        (user_agg['leader_passed_count'].sum()),
        total_leader_passed_duration_seconds=user_agg[
        'leader_passed_duration_seconds'].sum(),
        total_leader_passed_duration_display=format_duration(user_agg[
        'leader_passed_duration_seconds'].sum()),
        total_leader_returned_count=int(user_agg['leader_returned_count'].
        sum()), total_leader_returned_duration_seconds=user_agg[
        'leader_returned_duration_seconds'].sum(),
        total_leader_returned_duration_display=format_duration(user_agg[
        'leader_returned_duration_seconds'].sum()),
        total_auditor_passed_count=int(user_agg['auditor_passed_count'].sum
        ()), total_auditor_passed_duration_seconds=user_agg[
        'auditor_passed_duration_seconds'].sum(),
        total_auditor_passed_duration_display=format_duration(user_agg[
        'auditor_passed_duration_seconds'].sum()),
        total_auditor_returned_count=int(user_agg['auditor_returned_count']
        .sum()), total_auditor_returned_duration_seconds=user_agg[
        'auditor_returned_duration_seconds'].sum(),
        total_auditor_returned_duration_display=format_duration(user_agg[
        'auditor_returned_duration_seconds'].sum()), user_summaries=
        user_summaries)
    return {'summary': summary, 'user_data': user_agg, 'weekly_breakdown':
        weekly_breakdown, 'weekly_totals': weekly_totals, 'daily_breakdown':
        daily_breakdown, 'daily_totals': daily_totals, 'previous_month':
        prev_month_data, 'raw_data': monthly_df}


def _get_previous_month_summary(df: pd.DataFrame, current_month: str,
    user_column: str, duration_column: str) ->(dict[str, Any] | None):
    """Get summary data for the month before the current one."""
    if 'completed_month' not in df.columns:
        return None
    months = sorted(df['completed_month'].dropna().unique())
    if current_month not in months:
        return None
    idx = list(months).index(current_month)
    if idx == 0:
        return None
    prev_month = months[idx - 1]
    prev_df = df[df['completed_month'] == prev_month]
    if prev_df.empty:
        return None
    prev_total = prev_df[duration_column].sum()
    prev_tasks = len(prev_df)
    return {'month': prev_month, 'total_tasks': prev_tasks,
        'total_duration_seconds': prev_total, 'total_duration_display':
        format_duration(prev_total)}


def _empty_monthly_report(month: str) ->dict[str, Any]:
    """Create an empty monthly report placeholder."""
    return {'summary': ReportSummary(report_type='monthly', period_label=
        month, period_start=f'{month}-01', period_end=f'{month}-28'),
        'user_data': pd.DataFrame(), 'weekly_breakdown': pd.DataFrame(),
        'weekly_totals': pd.DataFrame(), 'daily_breakdown': pd.DataFrame(),
        'daily_totals': pd.DataFrame(), 'previous_month': None, 'raw_data':
        pd.DataFrame()}
