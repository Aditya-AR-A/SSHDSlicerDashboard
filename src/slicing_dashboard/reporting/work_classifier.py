"""
Work Classifier and Duration Reporting Module.

Automates the classification and calculation of:
- New Video work vs Rework
- First and last submission timestamps and video IDs
- Total duration (in seconds, HH:MM:SS, and hours) per batch and individual
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from slicing_dashboard.config import PROJECT_ROOT, get_settings
from slicing_dashboard.scraper.http_scraper import HTTPScraper


def format_duration_hms(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_user_mapping() -> dict[str, str]:
    """Load canonical user mapping from config/user_mapping.json."""
    mapping_file = PROJECT_ROOT / "config" / "user_mapping.json"
    if mapping_file.exists():
        with open(mapping_file) as f:
            return json.load(f)
    return {}


class WorkClassifier:
    """Classifies slicing tasks as New Video or Rework and calculates performance."""

    def __init__(self, scraper: Optional[HTTPScraper] = None):
        self.settings = get_settings()
        self.scraper = scraper or HTTPScraper(self.settings)
        self.user_mapping = get_user_mapping()
        self._authenticated = False
        self._users_cache: Optional[list[dict[str, Any]]] = None
        self._tasks_cache: dict[str, list[dict[str, Any]]] = {}
        self._rework_cache: Optional[set[str]] = None

    def _ensure_authenticated(self) -> bool:
        if not self._authenticated:
            self._authenticated = self.scraper.login()
        return self._authenticated

    def get_user_accounts(self, canonical_name: str) -> dict[int, str]:
        """Get all user IDs and usernames mapped to a canonical individual."""
        if not self._ensure_authenticated():
            return {}

        if self._users_cache is None:
            resp = self.scraper._client.get(f"{self.scraper._base_url}/api/users")
            self._users_cache = resp.json().get("items", [])

        # Match canonical name (case-insensitive)
        matched = {}
        for item in self._users_cache:
            username = item.get("username", "")
            mapped_name = self.user_mapping.get(username, username)
            if mapped_name.lower() == canonical_name.lower() or username.lower() == canonical_name.lower():
                matched[item.get("id")] = username
        return matched

    def fetch_tasks_for_date(self, date_str: str) -> list[dict[str, Any]]:
        """Fetch all tasks updated on a specific date (YYYY-MM-DD)."""
        if not self._ensure_authenticated():
            return []

        if date_str in self._tasks_cache:
            return self._tasks_cache[date_str]

        tasks_on_date = []
        page = 1
        client = self.scraper._client
        base_url = self.scraper._base_url

        while True:
            resp = client.get(f"{base_url}/api/slice/tasks", params={"page_size": 200, "page": page})
            data = resp.json().get("data", [])
            if not data:
                break
            hit_older = False
            for task in data:
                updated_at = task.get("updated_at", "")
                if updated_at.startswith(date_str):
                    tasks_on_date.append(task)
                elif updated_at < date_str:
                    hit_older = True
            
            if hit_older or not resp.json().get("meta", {}).get("has_more", False):
                break
            
            # Safety break if we get too far deep (e.g. page > 50)
            if page > 50:
                break
            page += 1

        self._tasks_cache[date_str] = tasks_on_date
        return tasks_on_date

    def fetch_rework_requests(self) -> set[str]:
        """Fetch all rework notices from /api/requests."""
        if not self._ensure_authenticated():
            return set()

        if self._rework_cache is not None:
            return self._rework_cache

        rework_ids = set()
        page = 1
        client = self.scraper._client
        base_url = self.scraper._base_url

        while True:
            resp = client.get(
                f"{base_url}/api/requests",
                params={"workflow_type": "slice", "page_size": 200, "page": page},
            )
            items = resp.json().get("data", [])
            if not items:
                break
            for req in items:
                if req.get("request_type") == "rework_notice":
                    rework_ids.add(req.get("task_id"))
            if not resp.json().get("meta", {}).get("has_more", False):
                break
            page += 1

        self._rework_cache = rework_ids
        return rework_ids

    def classify_work(
        self,
        canonical_name: str,
        target_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Classify all work done by an individual on a target date into New Video vs Rework.
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        user_accounts = self.get_user_accounts(canonical_name)
        if not user_accounts:
            return {"error": f"No user accounts found for {canonical_name}", "canonical_name": canonical_name}

        rework_task_ids = self.fetch_rework_requests()
        all_tasks = self.fetch_tasks_for_date(target_date)

        # Filter for user accounts
        user_tasks = [
            t for t in all_tasks
            if t.get("slicer_id") in user_accounts or t.get("slicer") in user_accounts.values()
        ]

        if not user_tasks:
            return {
                "canonical_name": canonical_name,
                "target_date": target_date,
                "accounts": user_accounts,
                "batches": [],
                "totals": {"new_videos_seconds": 0.0, "rework_seconds": 0.0},
            }

        df = pd.DataFrame(user_tasks)
        batch_summaries = []

        total_new_duration = 0.0
        total_rework_duration = 0.0
        total_new_tasks = 0
        total_rework_tasks = 0

        # Historical master for detecting prior slices
        slicing_master_path = PROJECT_ROOT / "data" / "processed" / "slicing_master.csv"
        master_df = pd.read_csv(slicing_master_path) if slicing_master_path.exists() else pd.DataFrame()

        for (username, batch_num), b_df in df.groupby(["slicer", "slice_batch"]):
            # Ignore batches still purely in assigned state (0 submitted)
            submitted_tasks = b_df[b_df["status"].isin([
                "slice_submitted", "slice_pending_auditor_review"
            ])].sort_values("updated_at")

            if submitted_tasks.empty:
                continue

            # Determine whether batch is Rework or New Videos:
            # 1. Any task has a rework notice in requests
            # 2. Batch was historically in slice_rework status
            # 3. Tasks have high rework version cycles (e.g. version >= 7 or version % 2 == 1 with prior returns)
            has_rework_requests = any(t_id in rework_task_ids for t_id in submitted_tasks["id"])
            max_version = submitted_tasks["version"].max()

            is_rework = False
            if has_rework_requests:
                is_rework = True
            elif not master_df.empty:
                hist = master_df[(master_df["slicer"] == username) & (master_df["slice_batch"] == batch_num)]
                if not hist.empty and "slice_rework" in hist["status"].values:
                    is_rework = True

            first_task = submitted_tasks.iloc[0]
            last_task = submitted_tasks.iloc[-1]

            batch_duration = float(submitted_tasks["duration_seconds"].sum())
            batch_task_count = len(submitted_tasks)

            classification = "Rework" if is_rework else "New Video"

            if is_rework:
                total_rework_duration += batch_duration
                total_rework_tasks += batch_task_count
            else:
                total_new_duration += batch_duration
                total_new_tasks += batch_task_count

            batch_summaries.append({
                "username": username,
                "slice_batch": int(batch_num),
                "classification": classification,
                "task_count": batch_task_count,
                "duration_seconds": batch_duration,
                "duration_hms": format_duration_hms(batch_duration),
                "duration_hours": round(batch_duration / 3600.0, 2),
                "first_submission": {
                    "task_id": first_task["id"],
                    "video_id": first_task["video_id"],
                    "time_cst": first_task["updated_at"],
                    "duration_seconds": float(first_task["duration_seconds"]),
                    "status": first_task["status"],
                    "version": int(first_task["version"]),
                },
                "last_submission": {
                    "task_id": last_task["id"],
                    "video_id": last_task["video_id"],
                    "time_cst": last_task["updated_at"],
                    "duration_seconds": float(last_task["duration_seconds"]),
                    "status": last_task["status"],
                    "version": int(last_task["version"]),
                },
            })

        return {
            "canonical_name": canonical_name,
            "target_date": target_date,
            "accounts": user_accounts,
            "batches": batch_summaries,
            "totals": {
                "new_videos_tasks": total_new_tasks,
                "new_videos_seconds": total_new_duration,
                "new_videos_hms": format_duration_hms(total_new_duration),
                "new_videos_hours": round(total_new_duration / 3600.0, 2),
                "rework_tasks": total_rework_tasks,
                "rework_seconds": total_rework_duration,
                "rework_hms": format_duration_hms(total_rework_duration),
                "rework_hours": round(total_rework_duration / 3600.0, 2),
                "combined_tasks": total_new_tasks + total_rework_tasks,
                "combined_seconds": total_new_duration + total_rework_duration,
                "combined_hms": format_duration_hms(total_new_duration + total_rework_duration),
                "combined_hours": round((total_new_duration + total_rework_duration) / 3600.0, 2),
            },
        }

    def classify_all_users(self, target_date: Optional[str] = None) -> pd.DataFrame:
        """Classify work for all canonical users on target_date into New Video vs Rework."""
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        all_tasks = self.fetch_tasks_for_date(target_date)
        if not all_tasks:
            return pd.DataFrame()

        rework_task_ids = self.fetch_rework_requests()

        slicing_master_path = PROJECT_ROOT / "data" / "processed" / "slicing_master.csv"
        master_df = pd.read_csv(slicing_master_path) if slicing_master_path.exists() else pd.DataFrame()

        results: dict[str, dict[str, Any]] = {}
        df = pd.DataFrame(all_tasks)

        for (username, batch_num), b_df in df.groupby(["slicer", "slice_batch"]):
            submitted_tasks = b_df[b_df["status"].isin([
                "slice_submitted", "slice_pending_auditor_review"
            ])]
            if submitted_tasks.empty:
                continue

            canonical = username
            for k, v in self.user_mapping.items():
                if k.lower() == username.lower():
                    canonical = v
                    break
            if canonical in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)"]:
                continue

            if canonical not in results:
                results[canonical] = {
                    "User": canonical,
                    "New Work Duration": 0.0,
                    "Rework Duration": 0.0,
                    "New Tasks": 0,
                    "Rework Tasks": 0,
                }

            has_rework_requests = any(t_id in rework_task_ids for t_id in submitted_tasks["id"])
            max_version = submitted_tasks["version"].max()
            is_rework = False
            if has_rework_requests:
                is_rework = True
            elif not master_df.empty:
                hist = master_df[(master_df["slicer"] == username) & (master_df["slice_batch"] == batch_num)]
                if not hist.empty and "slice_rework" in hist["status"].values:
                    is_rework = True

            batch_duration = float(submitted_tasks["duration_seconds"].sum())
            batch_count = len(submitted_tasks)

            if is_rework:
                results[canonical]["Rework Duration"] += batch_duration
                results[canonical]["Rework Tasks"] += batch_count
            else:
                results[canonical]["New Work Duration"] += batch_duration
                results[canonical]["New Tasks"] += batch_count

        records = list(results.values())
        for r in records:
            r["Total Work Duration"] = r["New Work Duration"] + r["Rework Duration"]
        return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(description="Classify work into New Video and Rework for an individual.")
    parser.add_argument("--user", required=True, help="Canonical user name (e.g. Aditya, Ranjeeta, Komal, Priya)")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    classifier = WorkClassifier()
    res = classifier.classify_work(args.user, args.date)

    if args.json:
        print(json.dumps(res, indent=2))
        return

    print(f"\n================================================================================")
    print(f" WORK REPORT: {res['canonical_name']} (Date: {res['target_date']})")
    print(f"================================================================================")
    print(f"Mapped Accounts: {', '.join(res['accounts'].values())}\n")

    batches = res.get("batches", [])
    if not batches:
        print("No work submitted on this date.")
        return

    for b in batches:
        print(f"[{b['classification'].upper()}] {b['username']} - Batch {b['slice_batch']}")
        print(f"  Tasks: {b['task_count']} | Duration: {b['duration_hms']} ({b['duration_hours']} hrs / {b['duration_seconds']}s)")
        print(f"  First Submitted : {b['first_submission']['time_cst']} | Video: {b['first_submission']['video_id']} ({b['first_submission']['duration_seconds']}s)")
        print(f"  Last Submitted  : {b['last_submission']['time_cst']} | Video: {b['last_submission']['video_id']} ({b['last_submission']['duration_seconds']}s)")
        print()

    print("--------------------------------------------------------------------------------")
    print(f"TOTALS:")
    print(f"  New Videos : {res['totals']['new_videos_tasks']} tasks | {res['totals']['new_videos_hms']} ({res['totals']['new_videos_hours']} hrs)")
    print(f"  Rework     : {res['totals']['rework_tasks']} tasks | {res['totals']['rework_hms']} ({res['totals']['rework_hours']} hrs)")
    print(f"  Combined   : {res['totals']['combined_tasks']} tasks | {res['totals']['combined_hms']} ({res['totals']['combined_hours']} hrs)")
    print("================================================================================\n")


if __name__ == "__main__":
    main()
