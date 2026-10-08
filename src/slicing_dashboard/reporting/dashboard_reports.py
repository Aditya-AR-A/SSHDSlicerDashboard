"""Prepared daily reporting data, based only on observed submission evidence.

Dates without a successful capture remain unavailable. Earlier closed dates
remain immutable because later inventories cannot reconstruct all submissions.
Yesterday's end-of-day reconciliation can add late submissions while retaining
earlier evidence. All work amounts are video seconds, never operator time spent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import math
from typing import Any

import pandas as pd

from slicing_dashboard.processing.daily_work import EXCLUDED, format_video_seconds, instant


BUCKETS = ("Fresh", "Same-day Rework", "Old Rework")
TABLE_COLUMNS = ["User", "Total Tasks", "Total Duration", "New Videos (First Time)",
                 "Same-day Rework", "Old Rework", "New Tasks", "Same-day Rework Tasks",
                 "Old Rework Tasks", "Rework %", "RawID"]
EXCLUDED_NAMES = {str(name).casefold() for name in EXCLUDED} | {
    "unassigned", "unknown", "none", "total", "all slicers",
}


def json_safe(value: Any) -> Any:
    """Remove pandas/numpy scalar types and nonfinite values from UI payloads."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def reporting_user(name: Any) -> bool:
    clean = str(name or "").strip()
    return bool(clean) and clean.casefold() not in EXCLUDED_NAMES and not clean.casefold().startswith("user-none")


def report_users(mappings, snapshot=None, history=None, mapping_records=None) -> list[str]:
    """Known people plus observed unmapped named accounts; include zero-work people."""
    excluded_accounts = {str(row.get("id", "")).casefold() for row in (mapping_records or [])
                         if row.get("mapping_type") in ("Exempt", "New")}
    names = [name for account, name in (mappings or {}).items()
             if str(account).casefold() not in excluded_accounts]
    allowed_names = {str(name).casefold() for name in names}
    excluded_names = {str(row.get('mapped_user') or '').casefold() for row in (mapping_records or [])
                      if row.get('mapping_type') in ('Exempt', 'New')} - allowed_names
    names += list((snapshot or {}).get("available_users", []))
    for day in history or []:
        names += [row.get("User") for row in day.get("rows", [])]
    unique = {}
    for name in names:
        if reporting_user(name) and str(name).casefold() not in excluded_names:
            unique.setdefault(str(name).strip().casefold(), str(name).strip())
    return sorted(unique.values(), key=str.casefold)


def date_range(end_date: str, days: int = 30) -> list[str]:
    end = date.fromisoformat(end_date)
    if days < 1:
        raise ValueError("The report range must contain at least one day.")
    return [(end - timedelta(days=offset)).isoformat() for offset in reversed(range(days))]


def unavailable_day(day: str, error=None) -> dict:
    metadata = {"target_date": day, "timezone": "Asia/Kolkata", "available": False,
                "coverage": "unavailable", "is_snapshot": False}
    if error:
        metadata["error"] = str(error)
    return {"date": day, "rows": [], "metadata": metadata}


def aggregate_observations(tasks, canonical_name=None) -> list[dict]:
    """Aggregate one preserved evidence entry per task/day using current mappings."""
    stats = defaultdict(lambda: defaultdict(float))
    accounts = defaultdict(set)
    for task in tasks:
        raw = str(task.get("username") or "")
        user = canonical_name(task.get("user_id"), raw) if canonical_name else task.get("user", raw)
        if not reporting_user(user):
            continue
        bucket = task.get("bucket")
        seconds = float(task.get("duration_seconds") or 0)
        if bucket not in BUCKETS or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Daily work evidence has an invalid bucket or video duration.")
        stats[user][bucket] += seconds
        stats[user][bucket + " Count"] += 1
        accounts[user].add(raw)
    rows = []
    for user in sorted(stats, key=str.casefold):
        values = stats[user]
        fresh, same, old = (values[bucket] for bucket in BUCKETS)
        total = fresh + same
        counts = [int(values[bucket + " Count"]) for bucket in BUCKETS]
        rows.append({"User": user, "Total Tasks": sum(counts), "Total Duration": total,
                     "New Videos (First Time)": fresh, "Same-day Rework": same, "Old Rework": old,
                     "Reworks": same, "New Tasks": counts[0], "Same-day Rework Tasks": counts[1],
                     "Old Rework Tasks": counts[2],
                     "Rework %": f"{same / total * 100:.1f}%" if total else "0.0%",
                     "RawID": ",".join(sorted(accounts[user]))})
    return rows


def merge_daily_reports(existing: dict | None, incoming: dict) -> dict:
    """Union task/day observations without altering a previously captured closed day.

    ``observed_on`` is the IST date of the current capture, not the requested
    historical date. Historical bootstraps are allowed only for an absent day.
    """
    day = incoming["date"]
    date.fromisoformat(day)
    if incoming.get('metadata', {}).get('available') is False:
        raise ValueError('Unavailable data is not a successful daily observation.')
    observed_on = incoming.get("metadata", {}).get("observed_on", day)
    metadata_in = incoming.get('metadata', {})
    captured = instant(metadata_in.get('captured_at'))
    reconcile = (metadata_in.get('capture_kind') == 'day_end_reconciliation'
                 and metadata_in.get('target_date') == day
                 and observed_on == (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                 and captured is not None and captured.date().isoformat() == observed_on
                 and isinstance(incoming.get('tasks'), list))
    if existing and (existing.get("metadata", {}).get("closed") or day < observed_on) and not reconcile:
        return json_safe(existing)
    evidence = {}
    for record in (existing or {}, incoming):
        for task in record.get("tasks", []):
            identity = str(task["task_id"])
            stamp = instant(task.get("submitted_at"))
            if stamp is None or stamp.date().isoformat() != day:
                raise ValueError("Task evidence does not belong to its report date.")
            observed_at = task.get("observed_at") or record.get("metadata", {}).get("captured_at") or task["submitted_at"]
            previous = evidence.get(identity)
            if previous is None or (stamp, instant(observed_at)) >= (
                    instant(previous["submitted_at"]), instant(previous["observed_at"])):
                evidence[identity] = dict(task, task_id=identity, observed_at=observed_at)
    tasks = [evidence[identity] for identity in sorted(evidence)]
    metadata = {**(existing or {}).get("metadata", {}), **incoming.get("metadata", {}),
                "available": True, "coverage": "observed", "closed": day < observed_on,
                "target_date": day, "timezone": "Asia/Kolkata"}
    captures = [record.get("metadata", {}).get("captured_at") for record in (existing or {}, incoming)]
    captures = [stamp for stamp in captures if stamp]
    if captures:
        metadata["captured_at"] = max(captures, key=instant)
    if reconcile:
        metadata['day_end_reconciled_at'] = metadata_in['captured_at']
    if existing:
        metadata["first_captured_at"] = existing.get("metadata", {}).get(
            "first_captured_at", existing.get("metadata", {}).get("captured_at"))
    else:
        metadata["first_captured_at"] = metadata.get("captured_at")
    result = {"date": day, "rows": aggregate_observations(tasks), "metadata": metadata}
    if "tasks" in incoming or (existing and "tasks" in existing):
        result["tasks"] = tasks
    else:
        # Older verified exports may contain only their preserved summary.
        result["rows"] = incoming.get("rows", (existing or {}).get("rows", []))
    return json_safe(result)


def normalize_daily_row(row: dict) -> dict:
    """Recalculate saved summary rows under the current total-work policy."""
    row = dict(row)
    if any(column in row for column in ('New Videos (First Time)', 'Same-day Rework', 'Old Rework')):
        new = float(row.get('New Videos (First Time)') or 0)
        same = float(row.get('Same-day Rework') or 0)
        row['Total Duration'] = new + same
        row['Reworks'] = same
        row['Rework %'] = f'{same / (new + same) * 100:.1f}%' if new + same else '0.0%'
    return row


def day_for_ui(record: dict | None, day: str, canonical_name=None) -> dict:
    if not record or record.get("metadata", {}).get("available") is False:
        return unavailable_day(day, (record or {}).get("metadata", {}).get("error"))
    rows = aggregate_observations(record["tasks"], canonical_name) if "tasks" in record else record.get("rows", [])
    from slicing_dashboard.management.periods import today_iso
    metadata = record.get('metadata', {})
    # Passing midnight doesn't establish that the last capture included the
    # rest of the day. Preserve that distinction across every report consumer.
    reconciliation_pending = (day < today_iso() and
                              metadata.get('capture_kind') != 'verified_audit_import' and
                              not metadata.get('day_end_reconciled_at'))
    return json_safe({"date": day, "rows": [normalize_daily_row(row) for row in rows], "metadata": {
        **metadata, "available": True, 'reconciliation_pending': reconciliation_pending,
        "coverage": record.get("metadata", {}).get("coverage", "observed"),
    }})


def _rows(day, users=None):
    rows = day.get("rows", []) if day.get("metadata", {}).get("available") else []
    return [normalize_daily_row(row) for row in rows if reporting_user(row.get("User"))
            and (users is None or row.get("User") in users)]


def summarize_daily(day: dict, users=None) -> dict:
    """Prepared KPI amounts; unavailable differs from a captured zero-work day."""
    available = bool(day.get("metadata", {}).get("available"))
    rows = _rows(day, users)
    fields = {"total_seconds": "Total Duration", "new_seconds": "New Videos (First Time)",
              "same_day_rework_seconds": "Same-day Rework", "old_rework_seconds": "Old Rework",
              "total_tasks": "Total Tasks"}
    summary = {key: sum(float(row.get(column) or 0) for row in rows) if available else None
               for key, column in fields.items()}
    summary["active_users"] = sum(int(row.get("Total Tasks") or 0) > 0 for row in rows) if available else None
    if available:
        summary["total_tasks"] = int(summary["total_tasks"])
    summary["available"] = available
    summary["rework_seconds"] = summary["same_day_rework_seconds"] if available else None
    summary["rework_percentage"] = summary["rework_seconds"] / summary["total_seconds"] * 100 if summary["total_seconds"] else None
    return summary


def day_over_day(today: dict, yesterday: dict) -> dict:
    current, previous = summarize_daily(today), summarize_daily(yesterday)
    if not current["available"] or not previous["available"]:
        return {"difference_seconds": None, "percentage": None}
    difference = current["total_seconds"] - previous["total_seconds"]
    return {"difference_seconds": difference,
            "percentage": difference / previous["total_seconds"] * 100 if previous["total_seconds"] else None}


def prepare_daily_table(day: dict, users=None, numeric_durations=False) -> list[dict]:
    rows = [{column: row.get(column, "") for column in TABLE_COLUMNS} for row in _rows(day, users)]
    if not rows:
        return []
    total = {"User": "TOTAL", "RawID": ""}
    for column in TABLE_COLUMNS:
        if column not in ("User", "RawID", "Rework %"):
            total[column] = sum(float(row.get(column) or 0) for row in rows)
    reworks = total["Same-day Rework"]
    total["Rework %"] = f"{reworks / total['Total Duration'] * 100:.1f}%" if total["Total Duration"] else "0.0%"
    rows.append(total)
    for row in rows:
        for column in ("Total Duration", "New Videos (First Time)", "Same-day Rework", "Old Rework"):
            row[column] = float(row[column]) / 3600 if numeric_durations else format_video_seconds(row[column])
        for column in ("Total Tasks", "New Tasks", "Same-day Rework Tasks", "Old Rework Tasks"):
            row[column] = int(row[column])
    return rows


def prepare_daily_work_chart_rows(day: dict, users=None) -> pd.DataFrame:
    """Use the comparison's per-person totals and retain the verified zero roster."""
    columns = ['User', 'New Work Duration', 'Same-day Rework Duration', 'Old Rework Duration',
               'Total Work Duration', 'IDs', 'Rework Duration']
    if not day.get('metadata', {}).get('available'):
        return pd.DataFrame(columns=columns)
    source_rows = _rows(day, users)
    people = list(dict.fromkeys(users if users is not None else (row['User'] for row in source_rows)))
    rows = []
    for user in people:
        if not reporting_user(user):
            continue
        summary = summarize_daily(day, [user])
        accounts = {account.strip() for row in source_rows if row['User'] == user
                    for account in str(row.get('RawID') or '').split(',') if account.strip()}
        rows.append({'User': user, 'New Work Duration': summary['new_seconds'],
                     'Same-day Rework Duration': summary['same_day_rework_seconds'],
                     'Old Rework Duration': summary['old_rework_seconds'],
                     'Total Work Duration': summary['total_seconds'], 'IDs': ','.join(sorted(accounts)),
                     'Rework Duration': summary['same_day_rework_seconds']})
    return pd.DataFrame(rows, columns=columns)


def prepare_user_comparison_rows(history: list[dict], users: list[str]) -> list[dict]:
    result = []
    fields = ('total_seconds', 'new_seconds', 'same_day_rework_seconds', 'old_rework_seconds')
    for day in history:
        for user in dict.fromkeys(users):
            summary = summarize_daily(day, [user])
            result.append({"date": day["date"], "user": user,
                           **{key: summary[key] for key in fields}})
    return result


def prepare_team_composition_rows(history: list[dict]) -> list[dict]:
    fields = ("total_seconds", "new_seconds", "same_day_rework_seconds", "old_rework_seconds")
    result = []
    for day in history:
        summary = summarize_daily(day)
        result.append({"date": day["date"], **{key: summary[key] for key in fields}})
    return result


def get_daily_report_data(manager, report_date=None, force_refresh=False) -> dict:
    """Capture today and reconcile yesterday; earlier dates use saved observations."""
    from slicing_dashboard.management.periods import today_iso

    current_date = today_iso()
    report_date = report_date or current_date
    dates = date_range(report_date)
    if report_date > current_date:
        raise ValueError("The daily report cannot end after today in India time.")
    previous_date = (date.fromisoformat(report_date) - timedelta(days=1)).isoformat()
    yesterday_date = (date.fromisoformat(current_date) - timedelta(days=1)).isoformat()
    with manager.refresh_scope():
        records = manager._load_daily_report_records(dates[0], dates[-1])
        overrides = {}
        if report_date in (current_date, yesterday_date):
            for day in (report_date, previous_date):
                if day not in (current_date, yesterday_date):
                    continue
                stored = records.get(day, {})
                kind = stored.get('metadata', {}).get('capture_kind')
                reconcile = day == yesterday_date and kind != 'verified_audit_import' and (
                    force_refresh or kind != 'day_end_reconciliation')
                if day == current_date or reconcile:
                    frame = manager.get_todays_work_df(day, force_refresh=force_refresh or reconcile)
                    if frame.attrs.get("error"):
                        overrides[day] = {"date": day, "rows": frame.to_dict("records"),
                                          "metadata": dict(frame.attrs)}
            records.update(getattr(manager, "_daily_report_records", {}))
        canonical_name = getattr(manager, '_get_reporting_name', manager._get_canonical_name)
        history = [day_for_ui(records.get(day), day, canonical_name) for day in dates]
        for day in history:
            if day['metadata']['available']:
                day['metadata']['closed'] = day['date'] < current_date
            if day["date"] in overrides:
                day.update(day_for_ui(overrides[day["date"]], day["date"]))
        users = report_users(getattr(manager, "user_mapping", {}), getattr(manager, "_snapshot_payload", {}),
                             history, list(getattr(manager, "user_mapping_full", {}).values()))
        return json_safe({"report_date": report_date, "previous_date": previous_date,
                          "range_start": dates[0], "range_end": dates[-1], "users": users,
                          "today": history[-1], "yesterday": history[-2], "history": history})
