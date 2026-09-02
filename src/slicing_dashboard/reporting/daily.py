"""
Daily report generation.

Generates daily completion summaries:
- Total completed tasks and duration
- User-wise breakdown
- Per-task details
"""
from __future__ import annotations
from datetime import datetime
from typing import Any
import pandas as pd
from slicing_dashboard.extraction.parsers import format_duration
from slicing_dashboard.models.schemas import ReportSummary, UserSummary


def generate_daily_report(df: pd.DataFrame, date: (str | None)=None,
    user_column: str='user_name', duration_column: str='duration_seconds'
    ) ->dict[str, Any]:
    """Generate a daily report from processed data."""
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    if 'completed_date' not in df.columns:
        return _empty_report('daily', date, date)
    daily_df = df[(df['completed_date'] == date) & (df['completed_date'] >=
        '2026-07-07')].copy()
    if daily_df.empty:
        return _empty_report('daily', date, date)
    from slicing_dashboard.config import PROCESSED_DIR
    transitions_path = PROCESSED_DIR / 'transitions_master.csv'
    if transitions_path.exists():
        transitions_df = pd.read_csv(transitions_path)
        transitions_df['date'] = transitions_df['date'].astype(str)
        daily_transitions = transitions_df[transitions_df['date'] == date]
    else:
        daily_transitions = pd.DataFrame(columns=['task_id', 'user_name',
            'duration_seconds', 'date', 'type'])
    users_from_df = set(daily_df[user_column].dropna().unique())
    users_from_trans = set(daily_transitions['user_name'].dropna().unique())
    users_list = sorted(list(users_from_df.union(users_from_trans)))
    user_rows = []
    for user in users_list:
        user_df = daily_df[daily_df[user_column] == user
            ] if not daily_df.empty else pd.DataFrame()
        user_trans = daily_transitions[daily_transitions['user_name'] == user
            ] if not daily_transitions.empty else pd.DataFrame()
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
        total_comp_count = normal_count + error_count
        total_comp_dur = normal_dur + error_dur
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
            'total_completed_count': total_comp_count,
            'total_completed_duration_seconds': total_comp_dur,
            'total_completed_duration_display': format_duration(
            total_comp_dur), 'submitted_count': sub_count,
            'submitted_duration_seconds': sub_dur,
            'submitted_duration_display': format_duration(sub_dur),
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
    summary = ReportSummary(report_type='daily', period_label=date,
        period_start=date, period_end=date, total_completed_normal_count=
        int(user_agg['completed_normal_count'].sum()),
        total_completed_normal_duration_seconds=user_agg[
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
    return {'summary': summary, 'user_data': user_agg, 'daily_data': daily_df}


def _empty_report(report_type: str, start: str, end: str) ->dict[str, Any]:
    """Create an empty report placeholder."""
    return {'summary': ReportSummary(report_type=report_type, period_label=
        start if report_type == 'daily' else f'{start} to {end}',
        period_start=start, period_end=end), 'user_data': pd.DataFrame(),
        'daily_data': pd.DataFrame()}
