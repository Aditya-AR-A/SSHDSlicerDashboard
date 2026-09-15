"""
Individual Performance Report data generator.

Computes metrics isolated to a single individual's account/ID:
1. Individual Summary (overall completion, pending, rework, error rates)
2. Monthly Daily Distribution (day-by-day status + return source attribution)
3. Today's Activity (assigned, rework, pending, completed, first-time vs rework, partial batches)
4. Today's Video Detail (video-level audit records)
5. Underlying Helper Data
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from slicing_dashboard.config import PROJECT_ROOT
from slicing_dashboard.extraction.parsers import format_duration


def _load_user_mapping() -> dict[str, str]:
    """Load canonical user mapping from config/user_mapping.json."""
    mapping_path = PROJECT_ROOT / "config" / "user_mapping.json"
    if mapping_path.exists():
        try:
            with open(mapping_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _get_days_in_month(month_str: str) -> list[str]:
    """Return all date strings 'YYYY-MM-DD' for a given 'YYYY-MM'."""
    try:
        start_dt = datetime.strptime(f"{month_str}-01", "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now().replace(day=1)
    
    # Find start of next month
    if start_dt.month == 12:
        next_month = start_dt.replace(year=start_dt.year + 1, month=1)
    else:
        next_month = start_dt.replace(month=start_dt.month + 1)
    
    days_count = (next_month - start_dt).days
    return [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_count)]


def generate_individual_report_data(
    master_df: pd.DataFrame,
    transitions_df: pd.DataFrame | None,
    individual: str,
    target_date: str | None = None,
    target_month: str | None = None,
    user_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Build the complete data package for an individual's performance report.
    
    Args:
        master_df: Full slicing master DataFrame.
        transitions_df: Full transitions master DataFrame.
        individual: Canonical user name (e.g. "Priya") or specific username/account.
        target_date: Target date string 'YYYY-MM-DD'. Defaults to today or latest available.
        target_month: Target month string 'YYYY-MM'. Defaults to month of target_date.
        user_mapping: Optional dict mapping username -> canonical name.
    
    Returns:
        dict containing structured data for all 5 worksheets.
    """
    if user_mapping is None:
        user_mapping = _load_user_mapping()

    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    if target_month is None:
        target_month = target_date[:7]

    # Normalize transitions_df
    if transitions_df is None or transitions_df.empty:
        transitions_df = pd.DataFrame(columns=["task_id", "user_name", "duration_seconds", "date", "type"])
    else:
        transitions_df = transitions_df.copy()
        transitions_df["date"] = transitions_df["date"].astype(str)

    # Resolve individual aliases
    aliases = {individual}
    for uname, canon in user_mapping.items():
        if canon.lower() == individual.lower():
            aliases.add(uname)
    
    canonical_name = user_mapping.get(individual, individual)
    for uname, canon in user_mapping.items():
        if canon.lower() == canonical_name.lower():
            aliases.add(uname)

    # Filter master_df for this individual
    mask_master = pd.Series(False, index=master_df.index)
    for col in ["user_name", "user_id", "slicer"]:
        if col in master_df.columns:
            mask_master = mask_master | master_df[col].astype(str).isin(aliases)
    user_master_df = master_df[mask_master].copy()

    # Filter transitions_df for this individual
    mask_trans = transitions_df["user_name"].astype(str).isin(aliases)
    user_trans_df = transitions_df[mask_trans].copy()

    user_task_ids = set(user_master_df["id"].dropna().unique())
    if user_task_ids and not transitions_df.empty:
        mask_trans_by_task = transitions_df["task_id"].isin(user_task_ids)
        user_trans_df = pd.concat([user_trans_df, transitions_df[mask_trans_by_task]]).drop_duplicates()

    # ------------------------------------------------------------------
    # 1. Individual Summary Sheet Data
    # ------------------------------------------------------------------
    total_assigned_df = user_master_df[user_master_df["status"].isin(["slice_assigned", "slicing"])]
    total_assigned_count = len(total_assigned_df)
    total_assigned_dur = float(total_assigned_df["duration_seconds"].sum()) if not total_assigned_df.empty else 0.0

    comp_norm_df = user_master_df[
        (user_master_df["is_completed"] == True) & (user_master_df["completion_type"] == "normal")
    ]
    comp_norm_count = len(comp_norm_df)
    comp_norm_dur = float(comp_norm_df["duration_seconds"].sum()) if not comp_norm_df.empty else 0.0

    comp_err_df = user_master_df[
        (user_master_df["is_completed"] == True)
        & (
            (user_master_df["completion_type"] == "error")
            | (user_master_df["status"] == "video_error_confirmed")
        )
    ]
    comp_err_count = len(comp_err_df)
    comp_err_dur = float(comp_err_df["duration_seconds"].sum()) if not comp_err_df.empty else 0.0

    total_comp_count = comp_norm_count + comp_err_count
    total_comp_dur = comp_norm_dur + comp_err_dur

    pend_leader_df = user_master_df[user_master_df["status"] == "slice_submitted"]
    pend_leader_count = len(pend_leader_df)
    pend_leader_dur = float(pend_leader_df["duration_seconds"].sum()) if not pend_leader_df.empty else 0.0

    pend_auditor_df = user_master_df[user_master_df["status"] == "slice_pending_auditor_review"]
    pend_auditor_count = len(pend_auditor_df)
    pend_auditor_dur = float(pend_auditor_df["duration_seconds"].sum()) if not pend_auditor_df.empty else 0.0

    pend_admin_df = user_master_df[user_master_df["status"] == "slice_pending_admin_review"]
    pend_admin_count = len(pend_admin_df)
    pend_admin_dur = float(pend_admin_df["duration_seconds"].sum()) if not pend_admin_df.empty else 0.0

    total_pending_count = pend_leader_count + pend_auditor_count + pend_admin_count
    total_pending_dur = pend_leader_dur + pend_auditor_dur + pend_admin_dur

    rework_df = user_master_df[
        (user_master_df["status"] == "slice_rework") | (user_master_df["rework_by"].fillna("") != "")
    ]
    total_rework_count = len(rework_df)
    total_rework_dur = float(rework_df["duration_seconds"].sum()) if not rework_df.empty else 0.0

    # Workflow transition metrics
    sub_tasks = user_trans_df[user_trans_df["type"] == "submitted"]
    sub_count = len(sub_tasks)
    sub_dur = float(sub_tasks["duration_seconds"].sum()) if not sub_tasks.empty else 0.0

    lp_tasks = user_trans_df[user_trans_df["type"] == "leader_passed"]
    lp_count = len(lp_tasks)
    lp_dur = float(lp_tasks["duration_seconds"].sum()) if not lp_tasks.empty else 0.0

    ap_tasks = user_trans_df[user_trans_df["type"] == "auditor_passed"]
    ap_count = len(ap_tasks)
    ap_dur = float(ap_tasks["duration_seconds"].sum()) if not ap_tasks.empty else 0.0

    # Performance percentages
    total_handled = total_comp_count + total_pending_count + total_assigned_count
    comp_pct = (total_comp_count / total_handled * 100.0) if total_handled > 0 else 0.0
    err_pct = (comp_err_count / total_comp_count * 100.0) if total_comp_count > 0 else 0.0
    rework_pct = (total_rework_count / total_handled * 100.0) if total_handled > 0 else 0.0
    avg_task_dur = (total_comp_dur / total_comp_count) if total_comp_count > 0 else 0.0

    summary_metrics = {
        "individual": canonical_name,
        "aliases": sorted(list(aliases)),
        "total_assigned_count": total_assigned_count,
        "total_assigned_duration_seconds": total_assigned_dur,
        "total_assigned_duration_display": format_duration(total_assigned_dur),
        "completed_normal_count": comp_norm_count,
        "completed_normal_duration_seconds": comp_norm_dur,
        "completed_normal_duration_display": format_duration(comp_norm_dur),
        "completed_error_count": comp_err_count,
        "completed_error_duration_seconds": comp_err_dur,
        "completed_error_duration_display": format_duration(comp_err_dur),
        "total_completed_count": total_comp_count,
        "total_completed_duration_seconds": total_comp_dur,
        "total_completed_duration_display": format_duration(total_comp_dur),
        "pending_leader_count": pend_leader_count,
        "pending_leader_duration_seconds": pend_leader_dur,
        "pending_leader_duration_display": format_duration(pend_leader_dur),
        "pending_auditor_count": pend_auditor_count,
        "pending_auditor_duration_seconds": pend_auditor_dur,
        "pending_auditor_duration_display": format_duration(pend_auditor_dur),
        "pending_admin_count": pend_admin_count,
        "pending_admin_duration_seconds": pend_admin_dur,
        "pending_admin_duration_display": format_duration(pend_admin_dur),
        "total_pending_count": total_pending_count,
        "total_pending_duration_seconds": total_pending_dur,
        "total_pending_duration_display": format_duration(total_pending_dur),
        "total_rework_count": total_rework_count,
        "total_rework_duration_seconds": total_rework_dur,
        "total_rework_duration_display": format_duration(total_rework_dur),
        "submitted_count": sub_count,
        "submitted_duration_seconds": sub_dur,
        "submitted_duration_display": format_duration(sub_dur),
        "leader_passed_count": lp_count,
        "leader_passed_duration_seconds": lp_dur,
        "leader_passed_duration_display": format_duration(lp_dur),
        "auditor_passed_count": ap_count,
        "auditor_passed_duration_seconds": ap_dur,
        "auditor_passed_duration_display": format_duration(ap_dur),
        "completion_rate_pct": round(comp_pct, 1),
        "error_rate_pct": round(err_pct, 1),
        "rework_rate_pct": round(rework_pct, 1),
        "avg_completed_duration_seconds": avg_task_dur,
        "avg_completed_duration_display": format_duration(avg_task_dur),
    }

    # ------------------------------------------------------------------
    # 2. Monthly Daily Distribution Sheet Data
    # ------------------------------------------------------------------
    all_month_days = _get_days_in_month(target_month)
    daily_rows = []

    m_master = user_master_df[
        user_master_df["completed_date"].fillna("").str.startswith(target_month)
    ].copy() if "completed_date" in user_master_df.columns else pd.DataFrame()

    m_trans = user_trans_df[
        user_trans_df["date"].fillna("").str.startswith(target_month)
    ].copy() if not user_trans_df.empty else pd.DataFrame()

    for day in all_month_days:
        day_master = m_master[m_master["completed_date"] == day] if not m_master.empty else pd.DataFrame()
        day_trans = m_trans[m_trans["date"] == day] if not m_trans.empty else pd.DataFrame()

        if not day_master.empty:
            d_norm = day_master[day_master["completion_type"] == "normal"]
            d_norm_cnt, d_norm_dur = len(d_norm), float(d_norm["duration_seconds"].sum())

            d_err = day_master[
                (day_master["completion_type"] == "error") | (day_master["status"] == "video_error_confirmed")
            ]
            d_err_cnt, d_err_dur = len(d_err), float(d_err["duration_seconds"].sum())

            d_pl = day_master[day_master["status"] == "slice_submitted"]
            d_pl_cnt, d_pl_dur = len(d_pl), float(d_pl["duration_seconds"].sum())

            d_paud = day_master[day_master["status"] == "slice_pending_auditor_review"]
            d_paud_cnt, d_paud_dur = len(d_paud), float(d_paud["duration_seconds"].sum())

            d_padm = day_master[day_master["status"] == "slice_pending_admin_review"]
            d_padm_cnt, d_padm_dur = len(d_padm), float(d_padm["duration_seconds"].sum())
        else:
            d_norm_cnt, d_norm_dur = 0, 0.0
            d_err_cnt, d_err_dur = 0, 0.0
            d_pl_cnt, d_pl_dur = 0, 0.0
            d_paud_cnt, d_paud_dur = 0, 0.0
            d_padm_cnt, d_padm_dur = 0, 0.0

        d_comp_cnt = d_norm_cnt + d_err_cnt
        d_comp_dur = d_norm_dur + d_err_dur

        if not day_trans.empty:
            lr_trans = day_trans[day_trans["type"] == "leader_returned"]
            lr_cnt, lr_dur = len(lr_trans), float(lr_trans["duration_seconds"].sum())

            ar_trans = day_trans[day_trans["type"] == "auditor_returned"]
            ar_cnt, ar_dur = len(ar_trans), float(ar_trans["duration_seconds"].sum())

            adm_trans = day_trans[day_trans["type"] == "admin_returned"]
            adm_cnt, adm_dur = len(adm_trans), float(adm_trans["duration_seconds"].sum())
        else:
            lr_cnt, lr_dur = 0, 0.0
            ar_cnt, ar_dur = 0, 0.0
            adm_cnt, adm_dur = 0, 0.0

        tot_ret_cnt = lr_cnt + ar_cnt + adm_cnt
        tot_ret_dur = lr_dur + ar_dur + adm_dur

        daily_rows.append({
            "Date": day,
            "Pending Leader Count": d_pl_cnt,
            "Pending Leader Hours": round(d_pl_dur / 3600.0, 2),
            "Pending Auditor Count": d_paud_cnt,
            "Pending Auditor Hours": round(d_paud_dur / 3600.0, 2),
            "Pending Admin Count": d_padm_cnt,
            "Pending Admin Hours": round(d_padm_dur / 3600.0, 2),
            "Completed Normal Count": d_norm_cnt,
            "Completed Normal Hours": round(d_norm_dur / 3600.0, 2),
            "Completed Error Count": d_err_cnt,
            "Completed Error Hours": round(d_err_dur / 3600.0, 2),
            "Total Completed Count": d_comp_cnt,
            "Total Completed Hours": round(d_comp_dur / 3600.0, 2),
            "Returned by Leader Count": lr_cnt,
            "Returned by Leader Hours": round(lr_dur / 3600.0, 2),
            "Returned by Auditor Count": ar_cnt,
            "Returned by Auditor Hours": round(ar_dur / 3600.0, 2),
            "Returned by Admin Count": adm_cnt,
            "Returned by Admin Hours": round(adm_dur / 3600.0, 2),
            "Total Returned Count": tot_ret_cnt,
            "Total Returned Hours": round(tot_ret_dur / 3600.0, 2),
        })

    monthly_daily_df = pd.DataFrame(daily_rows)

    # ------------------------------------------------------------------
    # 3. Today's Activity & 4. Video-Level Detail Data
    # ------------------------------------------------------------------
    today_mask = pd.Series(False, index=user_master_df.index)
    if "completed_date" in user_master_df.columns:
        today_mask = today_mask | (user_master_df["completed_date"] == target_date)
    if "updated_at" in user_master_df.columns:
        today_mask = today_mask | (user_master_df["updated_at"].fillna("").str.startswith(target_date))
    today_tasks_df = user_master_df[today_mask].copy()

    # Build historical rework and attempt signals (vectorized for speed)
    task_submit_counts = {}
    task_earliest_date = {}
    task_return_source = {}
    task_returned_set = set()

    if not transitions_df.empty and user_task_ids:
        user_trans_subset = transitions_df[transitions_df["task_id"].isin(user_task_ids)]
        if not user_trans_subset.empty:
            # Submission counts
            subs = user_trans_subset[user_trans_subset["type"] == "submitted"]
            task_submit_counts = subs.groupby("task_id").size().to_dict()
            
            # Earliest date per task
            task_earliest_date = user_trans_subset.groupby("task_id")["date"].min().to_dict()
            
            # Returns
            ret_lead = set(user_trans_subset[user_trans_subset["type"] == "leader_returned"]["task_id"])
            ret_aud = set(user_trans_subset[user_trans_subset["type"] == "auditor_returned"]["task_id"])
            task_returned_set = ret_lead | ret_aud
            for tid in ret_lead:
                task_return_source[tid] = "Leader"
            for tid in ret_aud:
                task_return_source[tid] = "Auditor"

    # Batch completion analysis (only for batches active today)
    all_batch_tasks = {}
    if "slice_batch" in master_df.columns and not today_tasks_df.empty:
        active_batches = set(today_tasks_df["slice_batch"].dropna().unique())
        if active_batches:
            batch_subset = master_df[master_df["slice_batch"].isin(active_batches)]
            for b_id, b_group in batch_subset.groupby("slice_batch"):
                if pd.notna(b_id) and b_id != 0:
                    all_batch_tasks[b_id] = {
                        "total_tasks": len(b_group),
                        "completed_task_ids": set(b_group[b_group["is_completed"] == True]["id"]),
                        "task_ids": set(b_group["id"]),
                    }

    video_detail_rows = []
    first_time_count = 0
    first_time_dur = 0.0
    rework_count = 0
    rework_dur = 0.0
    partial_batch_count = 0
    partial_batch_dur = 0.0

    today_completed_count = 0
    today_completed_dur = 0.0
    today_assigned_count = 0
    today_assigned_dur = 0.0
    today_pending_count = 0
    today_pending_dur = 0.0
    today_returned_count = 0
    today_returned_dur = 0.0

    for _, row in today_tasks_df.iterrows():
        t_id = row.get("id")
        v_id = row.get("video_id") or row.get("task_desensitize_id") or t_id
        b_id = row.get("slice_batch")
        u_id = row.get("user_id") or row.get("slicer") or individual
        status = row.get("status")
        dur_sec = float(row.get("duration_seconds") or 0.0)
        c_at = row.get("completed_at") or row.get("updated_at")
        rework_by_field = row.get("rework_by")

        prev_date = task_earliest_date.get(t_id, "")
        is_rework = bool(
            (pd.notna(rework_by_field) and str(rework_by_field).strip() != "")
            or (t_id in task_returned_set)
            or (task_submit_counts.get(t_id, 0) > 1)
            or (prev_date != "" and prev_date < target_date)
        )
        is_first_time = not is_rework

        returned_by = task_return_source.get(t_id, "")
        if not returned_by and pd.notna(rework_by_field) and str(rework_by_field).strip() != "":
            returned_by = str(rework_by_field).title()

        is_partial = False
        if pd.notna(b_id) and b_id in all_batch_tasks:
            b_info = all_batch_tasks[b_id]
            if len(b_info["completed_task_ids"]) < b_info["total_tasks"]:
                is_partial = True

        time_flag = "Valid"
        if dur_sec <= 0:
            time_flag = "Zero Duration"
        elif pd.isna(c_at):
            time_flag = "Missing Timestamp"

        is_comp = bool(row.get("is_completed") == True or status in ["slice_completed", "video_error_confirmed"])
        if is_comp:
            today_completed_count += 1
            today_completed_dur += dur_sec
        elif status in ["slice_assigned", "slicing"]:
            today_assigned_count += 1
            today_assigned_dur += dur_sec
        elif status in ["slice_submitted", "slice_pending_auditor_review", "slice_pending_admin_review"]:
            today_pending_count += 1
            today_pending_dur += dur_sec
        elif status == "slice_rework":
            today_returned_count += 1
            today_returned_dur += dur_sec

        if is_first_time:
            first_time_count += 1
            first_time_dur += dur_sec
        else:
            rework_count += 1
            rework_dur += dur_sec

        if is_partial:
            partial_batch_count += 1
            partial_batch_dur += dur_sec

        video_detail_rows.append({
            "Video ID": v_id,
            "Task ID": t_id,
            "Batch ID": b_id if pd.notna(b_id) else "N/A",
            "Account ID": u_id,
            "Work Status": status,
            "Completion Timestamp": str(c_at)[:19] if pd.notna(c_at) else "N/A",
            "Is First-Time": "Yes" if is_first_time else "No",
            "Is Rework": "Yes" if is_rework else "No",
            "Previous Worked Date": prev_date if (is_rework and prev_date) else "-",
            "Returned By": returned_by if returned_by else "-",
            "Attempts Count": task_submit_counts.get(t_id, 1),
            "Is Partial Batch": "Yes" if is_partial else "No",
            "Duration (Seconds)": dur_sec,
            "Duration (HH:MM:SS)": format_duration(dur_sec),
            "Duration (Hours)": round(dur_sec / 3600.0, 3),
            "Time Flag": time_flag,
        })

    today_video_detail_df = pd.DataFrame(video_detail_rows)

    # Today's Activity Summary Metrics
    total_today_work_dur = first_time_dur + rework_dur
    avg_comp_today = (today_completed_dur / today_completed_count) if today_completed_count > 0 else 0.0
    avg_first_time = (first_time_dur / first_time_count) if first_time_count > 0 else 0.0
    avg_rework = (rework_dur / rework_count) if rework_count > 0 else 0.0

    today_summary = {
        "target_date": target_date,
        "assigned_today_count": today_assigned_count,
        "assigned_today_hours": round(today_assigned_dur / 3600.0, 2),
        "rework_returned_today_count": today_returned_count,
        "rework_returned_today_hours": round(today_returned_dur / 3600.0, 2),
        "pending_review_today_count": today_pending_count,
        "pending_review_today_hours": round(today_pending_dur / 3600.0, 2),
        "completed_today_count": today_completed_count,
        "completed_today_hours": round(today_completed_dur / 3600.0, 2),
        "first_time_count": first_time_count,
        "first_time_hours": round(first_time_dur / 3600.0, 2),
        "rework_count": rework_count,
        "rework_hours": round(rework_dur / 3600.0, 2),
        "partial_batch_count": partial_batch_count,
        "partial_batch_hours": round(partial_batch_dur / 3600.0, 2),
        "total_time_spent_seconds": total_today_work_dur,
        "total_time_spent_display": format_duration(total_today_work_dur),
        "total_time_spent_hours": round(total_today_work_dur / 3600.0, 2),
        "avg_time_per_completed_display": format_duration(avg_comp_today),
        "avg_time_first_time_display": format_duration(avg_first_time),
        "avg_time_rework_display": format_duration(avg_rework),
    }

    # ------------------------------------------------------------------
    # 5. Data Helper Sheets
    # ------------------------------------------------------------------
    helper_cols = [
        "id", "video_id", "slice_batch", "user_name", "user_id", "status",
        "duration_seconds", "duration_display", "completed_date", "completed_at",
        "is_completed", "completion_type", "rework_by"
    ]
    present_cols = [c for c in helper_cols if c in user_master_df.columns]
    data_helper_tasks_df = user_master_df[present_cols].copy()
    data_helper_transitions_df = user_trans_df.copy()

    return {
        "individual": canonical_name,
        "aliases": sorted(list(aliases)),
        "target_date": target_date,
        "target_month": target_month,
        "summary": summary_metrics,
        "monthly_daily": monthly_daily_df,
        "today_summary": today_summary,
        "today_video_detail": today_video_detail_df,
        "data_helper_tasks": data_helper_tasks_df,
        "data_helper_transitions": data_helper_transitions_df,
    }
