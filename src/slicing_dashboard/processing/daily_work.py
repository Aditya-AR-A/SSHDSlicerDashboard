"""Daily output from actual task submissions, verified against returned batches.

Task updates and review approvals are not submission events. Video seconds are
not labor seconds. Each task contributes once using its latest submission.
"""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

INDIA = ZoneInfo("Asia/Kolkata")
UPSTREAM = ZoneInfo("Asia/Shanghai")
EXCLUDED = {"Admin", "Test", "Dep", "user-None", "", "(unassigned)", "Exempt"}


def format_video_seconds(seconds):
    value = 0 if seconds is None or pd.isna(seconds) else max(0, int(round(float(seconds))))
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def instant(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UPSTREAM)
    return stamp.astimezone(INDIA)


def batch_key(user, number):
    return str(user or ""), str(number) if number is not None else ""


def daily_submissions(tasks, target_date):
    unique = {task["id"]: task for task in tasks}
    return [task for task in unique.values()
            if (stamp := instant(task.get("slice_submitted_at"))) and stamp.date().isoformat() == target_date]


def aggregate_daily_work(tasks, returned_accounts, reviews, target_date, canonical_name, requests=None):
    returns = defaultdict(list)
    for account in returned_accounts:
        for event in account.get("data", []):
            returns[batch_key(account["username"], event.get("legacy_batch_number"))].append(event)
    prior_dates = {}
    for task in tasks:
        submitted = instant(task.get("slice_submitted_at"))
        if submitted:
            key = batch_key(task.get("slicer"), task.get("slice_batch"))
            prior_dates[key] = min(prior_dates.get(key, submitted.date().isoformat()), submitted.date().isoformat())
    for key, events in returns.items():
        for event in events:
            for review in reviews.get(event.get("batch_id"), {}).get("data", []):
                stamp = instant(review.get("reviewed_at"))
                if stamp:
                    prior_dates[key] = min(prior_dates.get(key, stamp.date().isoformat()), stamp.date().isoformat())
    stats = defaultdict(lambda: defaultdict(float))
    task_returns = defaultdict(list)
    for request in requests or []:
        kind = str(request.get("request_type") or "").lower()
        if request.get("workflow_type") == "slice" and any(term in kind for term in ("rework", "return", "reject")):
            task_returns[request.get("task_id")].append(request)
    evidence = []
    for task in daily_submissions(tasks, target_date):
        raw = task.get("slicer") or ""
        user = canonical_name(task.get("slicer_id"), raw)
        if user in EXCLUDED:
            continue
        submitted = instant(task["slice_submitted_at"])
        key = batch_key(raw, task.get("slice_batch"))
        before = [r for r in returns.get(key, []) if instant(r.get("returned_at")) and instant(r["returned_at"]) < submitted]
        individual_before = [r for r in task_returns.get(task["id"], [])
                             if instant(r.get("created_at")) and instant(r["created_at"]) < submitted]
        bucket = "Fresh"
        if before or individual_before:
            first = prior_dates.get(key)
            earliest_return = min([instant(r["returned_at"]).date().isoformat() for r in before]
                                  + [instant(r["created_at"]).date().isoformat() for r in individual_before])
            bucket = "Old Rework" if earliest_return < target_date or (first and first < target_date) else "Same-day Rework"
        duration = float(task.get("duration_seconds") or 0)
        if duration < 0:
            raise ValueError(f"Invalid duration for task {task['id']}")
        stats[user][bucket] += duration
        stats[user][bucket + " Count"] += 1
        stats[user].setdefault("accounts", set()).add(raw)
        evidence.append({"task_id": task["id"], "username": raw, "user": user, "slice_batch": task.get("slice_batch"),
                         "submitted_at": submitted.isoformat(), "duration_seconds": duration, "bucket": bucket,
                         "first_observed_batch_work_date": prior_dates.get(key),
                         "return_batch_ids": sorted({r["batch_id"] for r in before}),
                         "individual_return_request_ids": [r.get("id") for r in individual_before]})
    rows = []
    for user, values in sorted(stats.items()):
        fresh, same, old = (values[k] for k in ("Fresh", "Same-day Rework", "Old Rework"))
        count = sum(values[k + " Count"] for k in ("Fresh", "Same-day Rework", "Old Rework"))
        total = fresh + same + old
        rows.append({"User": user, "Total Tasks": int(count), "Total Duration": total,
                     "New Videos (First Time)": fresh, "Same-day Rework": same, "Old Rework": old,
                     "Reworks": same + old, "New Tasks": int(values["Fresh Count"]),
                     "Same-day Rework Tasks": int(values["Same-day Rework Count"]),
                     "Old Rework Tasks": int(values["Old Rework Count"]),
                     "Rework %": f"{(same + old) / total * 100:.1f}%" if total else "0.0%",
                     "RawID": ",".join(sorted(values["accounts"]))})
    return pd.DataFrame(rows), evidence
