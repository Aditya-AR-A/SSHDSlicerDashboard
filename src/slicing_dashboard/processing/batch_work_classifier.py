"""
Batch-First Work Classifier & Daily Consumption Processor.

Implements the domain rule:
1. Batch-Level Status Segregation: Each returned batch has comments/reasons attached
   (via /api/requests rework_notice, return comments, or review rejections).
   This completely segregates batches into Rework vs First-Time (New) batches.
2. Video-Level Time & Working Hours Aggregation: For each segregated batch,
   individual videos submitted on target_date provide exact duration_seconds
   and submission timestamps to compute total output and labor working hours.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from slicing_dashboard.config import PROJECT_ROOT, get_settings
from slicing_dashboard.scraper.http_scraper import HTTPScraper


def format_seconds_hms(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    if pd.isna(seconds) or seconds is None or seconds < 0:
        return "00:00:00"
    sec = int(round(float(seconds)))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_canonical_user_mapping() -> dict[str, str]:
    """Load canonical user mapping from config/user_mapping.json."""
    mapping_file = PROJECT_ROOT / "config" / "user_mapping.json"
    if mapping_file.exists():
        try:
            with open(mapping_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class BatchWorkClassifier:
    """Classifies batches into New Video vs Rework and aggregates video metrics."""

    SUBMITTED_STATUSES = {
        "slice_submitted",
        "slice_completed",
        "slice_pending_auditor_review",
        "slice_pending_admin_review",
        "video_error_confirmed",
    }

    def __init__(self, scraper: Optional[HTTPScraper] = None):
        self.settings = get_settings()
        self.scraper = scraper or HTTPScraper(self.settings)
        self.user_mapping = get_canonical_user_mapping()
        self._rework_requests_cache: Optional[dict[str, dict[str, Any]]] = None
        self._tasks_cache: dict[str, list[dict[str, Any]]] = {}

    def _ensure_authenticated(self) -> bool:
        if not self.scraper._authenticated:
            return self.scraper.login()
        return True

    def get_canonical_name(self, raw_name: str) -> str:
        """Map raw username/slicer to canonical individual name."""
        if not raw_name:
            return "(unassigned)"
        clean = raw_name.strip()
        for k, v in self.user_mapping.items():
            if k.lower() == clean.lower():
                return v
        if clean.startswith("SSHD-"):
            clean = clean[5:]
        return clean

    def fetch_rework_requests_with_comments(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Fetch all rework notices from /api/requests including comment/reason metadata.
        
        Returns:
            dict mapping task_id -> request dict (with reason, returned_by, created_at, etc.)
        """
        if not force_refresh and self._rework_requests_cache is not None:
            return self._rework_requests_cache

        if not self._ensure_authenticated():
            return {}

        rework_map: dict[str, dict[str, Any]] = {}
        client = self.scraper._client
        base_url = self.scraper._base_url
        page = 1

        try:
            while page <= 25:
                resp = client.get(
                    f"{base_url}/api/requests",
                    params={"workflow_type": "slice", "page_size": 200, "page": page},
                    timeout=6.0,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break
                for req in items:
                    req_type = req.get("request_type", "").lower()
                    if "rework" in req_type or req.get("returned_by") is not None or "reject" in req_type:
                        task_id = req.get("task_id")
                        if task_id:
                            rework_map[task_id] = {
                                "request_id": req.get("id"),
                                "request_type": req.get("request_type"),
                                "reason": req.get("reason") or "Rework required",
                                "returned_by": req.get("returned_by_name") or req.get("returned_by"),
                                "created_at": req.get("created_at"),
                            }
                if len(items) < 200:
                    break
                page += 1
        except Exception as e:
            print(f"Warning: Failed to fetch live rework requests: {e}")

        self._rework_requests_cache = rework_map
        return rework_map

    def load_historical_index(self, target_date: str) -> tuple[set[str], set[tuple[str, str]]]:
        """Load task IDs and (slicer, batch) keys that existed before target_date."""
        historical_task_ids: set[str] = set()
        historical_batches: set[tuple[str, str]] = set()

        master_path = PROJECT_ROOT / "data" / "processed" / "slicing_master.csv"
        if master_path.exists():
            try:
                mdf = pd.read_csv(master_path)
                if "completed_date" in mdf.columns:
                    older_mdf = mdf[mdf["completed_date"] < target_date]
                    historical_task_ids.update(older_mdf["id"].dropna().astype(str))
                    for _, r in older_mdf.iterrows():
                        historical_batches.add((str(r.get("slicer", "")), str(r.get("slice_batch", ""))))
            except Exception:
                pass

        trans_path = PROJECT_ROOT / "data" / "processed" / "transitions_master.csv"
        if trans_path.exists():
            try:
                tdf = pd.read_csv(trans_path)
                if "date" in tdf.columns:
                    older_tdf = tdf[tdf["date"] < target_date]
                    historical_task_ids.update(older_tdf["task_id"].dropna().astype(str))
            except Exception:
                pass

        return historical_task_ids, historical_batches

    def update_daily_tracker(self, target_date: str, batches_info: list[dict[str, Any]]) -> None:
        """Persist today's active batches and videos in data/daily_batches_tracker.json."""
        tracker_file = PROJECT_ROOT / "data" / "daily_batches_tracker.json"
        try:
            tracker_data = {}
            if tracker_file.exists():
                with open(tracker_file) as f:
                    tracker_data = json.load(f)

            day_records = tracker_data.get(target_date, {})
            for b in batches_info:
                b_key = f"{b['slicer']}_batch_{b['batch_num']}"
                if b_key not in day_records:
                    day_records[b_key] = {
                        "first_seen_at": datetime.now().isoformat(),
                        "classification": b["classification"],
                        "slicer": b["slicer"],
                        "batch_num": b["batch_num"],
                        "video_ids": list(b["video_ids"]),
                        "video_count": len(b["video_ids"]),
                        "total_duration": b["duration"],
                    }
                else:
                    day_records[b_key]["video_count"] = len(b["video_ids"])
                    day_records[b_key]["total_duration"] = b["duration"]

            tracker_data[target_date] = day_records
            with open(tracker_file, "w") as f:
                json.dump(tracker_data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to update daily tracker: {e}")

    def flush_daily_tracker(self, target_date: Optional[str] = None) -> None:
        """Reset the daily tracker for target_date or clean up old days."""
        tracker_file = PROJECT_ROOT / "data" / "daily_batches_tracker.json"
        if not tracker_file.exists():
            return
        try:
            with open(tracker_file) as f:
                tracker_data = json.load(f)
            if target_date and target_date in tracker_data:
                del tracker_data[target_date]
            else:
                tracker_data = {}
            with open(tracker_file, "w") as f:
                json.dump(tracker_data, f, indent=2)
        except Exception:
            pass

    def fetch_tasks_for_date(self, target_date: str, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Fetch all tasks submitted or updated on target_date (YYYY-MM-DD)."""
        if not force_refresh and target_date in self._tasks_cache:
            return self._tasks_cache[target_date]

        if not self._ensure_authenticated():
            return []

        tasks_on_date: list[dict[str, Any]] = []
        client = self.scraper._client
        base_url = self.scraper._base_url
        page = 1

        try:
            while page <= 50:
                resp = client.get(
                    f"{base_url}/api/slice/tasks",
                    params={"page_size": 200, "page": page},
                    timeout=6.0,
                )
                if resp.status_code != 200:
                    break
                res_data = resp.json()
                items = res_data.get("data", [])
                if not items:
                    break

                hit_older = False
                for t in items:
                    updated_at = t.get("updated_at", "")
                    if updated_at.startswith(target_date):
                        tasks_on_date.append(t)
                    elif updated_at and updated_at < target_date:
                        hit_older = True

                meta = res_data.get("meta", {})
                if hit_older or not meta.get("has_more", False):
                    break
                page += 1
        except Exception as e:
            print(f"Warning: Error fetching tasks for date {target_date}: {e}")

        # If live API returned tasks, cache and return
        if tasks_on_date:
            self._tasks_cache[target_date] = tasks_on_date
            return tasks_on_date

        # Fallback to local master data if live tasks list is empty or offline
        slicing_master = PROJECT_ROOT / "data" / "processed" / "slicing_master.csv"
        if slicing_master.exists():
            try:
                mdf = pd.read_csv(slicing_master)
                mask = pd.Series(False, index=mdf.index)
                if "completed_date" in mdf.columns:
                    mask = mask | (mdf["completed_date"] == target_date)
                if "updated_at" in mdf.columns:
                    mask = mask | (mdf["updated_at"].fillna("").str.startswith(target_date))
                subset = mdf[mask]
                if not subset.empty:
                    records = subset.to_dict(orient="records")
                    self._tasks_cache[target_date] = records
                    return records
            except Exception:
                pass

        return []

    def classify_and_aggregate_daily_work(
        self,
        target_date: Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Execute the two-stage Batch Segregation + Video Time Aggregation.
        
        Stage 1: Batch-Level Status Segregation
          - Identifies all batches worked today.
          - Checks for rework comments/reasons in requests, return status, or high version.
          - Categorizes each batch cleanly into 'Rework' or 'First-Time Work'.
          
        Stage 2: Video-Level Time & Working Hours Aggregation
          - For each batch, sums individual video duration_seconds submitted on target_date.
          - Aggregates by canonical user.
          - Calculates submission time spans (working hours) and slicing throughput.
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        tasks = self.fetch_tasks_for_date(target_date, force_refresh=force_refresh)
        if not tasks:
            return pd.DataFrame()

        rework_requests = self.fetch_rework_requests_with_comments(force_refresh=force_refresh)

        # Historical status fallback check
        slicing_master = PROJECT_ROOT / "data" / "processed" / "slicing_master.csv"
        master_df = pd.read_csv(slicing_master) if slicing_master.exists() else pd.DataFrame()

        df = pd.DataFrame(tasks)
        if "slicer" not in df.columns or "slice_batch" not in df.columns:
            return pd.DataFrame()

        # Filter for tasks that were actually submitted or completed today
        if "status" in df.columns:
            df = df[df["status"].isin(self.SUBMITTED_STATUSES)].copy()
        if df.empty:
            return pd.DataFrame()

        # Structure to collect canonical user statistics
        user_stats: dict[str, dict[str, Any]] = {}

        historical_task_ids, historical_batches = self.load_historical_index(target_date)

        batches_tracker_info = []

        # -------------------------------------------------------------
        # STAGE 1: Batch-Level Segregation
        # -------------------------------------------------------------
        for (slicer, batch_num), b_group in df.groupby(["slicer", "slice_batch"]):
            if pd.isna(batch_num) or str(batch_num) in ["0", "None", "nan"]:
                continue

            canonical = self.get_canonical_name(str(slicer))
            if canonical in ["Admin", "Test", "Dep", "user-None", "", "(unassigned)"]:
                continue

            batch_task_ids = set(b_group["id"].dropna())

            # Check for comments/reasons on returned batch:
            # 1. Any task ID in rework_requests (which has comment/reason attached)
            rework_reasons = []
            for tid in batch_task_ids:
                if tid in rework_requests:
                    rework_reasons.append(rework_requests[tid].get("reason", "Returned with comment"))

            has_rework_comments = len(rework_reasons) > 0

            # 2. Check for explicit rework statuses or rework_by field
            statuses = set(b_group["status"].dropna().unique())
            has_rework_status = "slice_rework" in statuses
            has_rework_by = bool(
                "rework_by" in b_group.columns
                and b_group["rework_by"].dropna().astype(str).str.strip().ne("").any()
            )

            # DETERMINISTIC DOMAIN RULE:
            # 1. Rework bracket ONLY contains videos returned after first work the day before or older.
            # 2. If a batch was first started/submitted today, it belongs in the New Videos bracket
            #    (even if returned and resubmitted today), because it is new work for today.
            prior_day_rework_notice = False
            for tid in batch_task_ids:
                if tid in rework_requests:
                    created_at = rework_requests[tid].get("created_at", "")
                    if created_at and created_at[:10] < target_date:
                        prior_day_rework_notice = True
                        break

            batch_key = (str(slicer), str(batch_num))
            existed_on_older_days = (
                any(tid in historical_task_ids for tid in batch_task_ids)
                or (batch_key in historical_batches)
            )

            is_rework_batch = bool(
                prior_day_rework_notice
                or (
                    existed_on_older_days
                    and (has_rework_comments or has_rework_status or has_rework_by)
                )
            )

            classification_label = "Rework" if is_rework_batch else "New Video"
            batch_dur_seconds = float(b_group["duration_seconds"].fillna(0).sum())
            batch_video_count = len(b_group)

            batches_tracker_info.append({
                "slicer": str(slicer),
                "batch_num": str(batch_num),
                "classification": classification_label,
                "video_ids": batch_task_ids,
                "duration": batch_dur_seconds,
            })

            # Extract timestamps for working hours calculation
            timestamps = []
            if "updated_at" in b_group.columns:
                timestamps = [str(t) for t in b_group["updated_at"].dropna() if str(t).startswith(target_date)]

            if canonical not in user_stats:
                user_stats[canonical] = {
                    "User": canonical,
                    "Total Tasks": 0,
                    "Total Duration Seconds": 0.0,
                    "New Videos Seconds": 0.0,
                    "Rework Seconds": 0.0,
                    "First Time Tasks": 0,
                    "Rework Tasks": 0,
                    "Timestamps": [],
                    "Rework Reasons": set(),
                    "RawIDs": set(),
                }

            user_stats[canonical]["Total Tasks"] += batch_video_count
            user_stats[canonical]["Total Duration Seconds"] += batch_dur_seconds
            user_stats[canonical]["Timestamps"].extend(timestamps)
            if slicer:
                user_stats[canonical]["RawIDs"].add(str(slicer))
            if rework_reasons:
                user_stats[canonical]["Rework Reasons"].update(rework_reasons)

            if is_rework_batch:
                user_stats[canonical]["Rework Seconds"] += batch_dur_seconds
                user_stats[canonical]["Rework Tasks"] += batch_video_count
            else:
                user_stats[canonical]["New Videos Seconds"] += batch_dur_seconds
                user_stats[canonical]["First Time Tasks"] += batch_video_count

        # Build output DataFrame
        rows = []
        for user, st in sorted(user_stats.items()):
            tot_sec = st["Total Duration Seconds"]
            new_sec = st["New Videos Seconds"]
            rew_sec = st["Rework Seconds"]
            tasks_cnt = st["Total Tasks"]
            
            raw_ids_list = list(st["RawIDs"])
            raw_id_str = raw_ids_list[0] if len(raw_ids_list) == 1 else (",".join(raw_ids_list) if raw_ids_list else "")

            # Compute Working Hours from submission timestamps
            ts_list = sorted(st["Timestamps"])
            working_hours_sec = 0.0
            if len(ts_list) >= 2:
                try:
                    t_first = datetime.fromisoformat(ts_list[0][:19])
                    t_last = datetime.fromisoformat(ts_list[-1][:19])
                    span = (t_last - t_first).total_seconds()
                    # Add a standard pacing allowance (e.g. 5 mins for the last batch)
                    working_hours_sec = max(span + 300.0, 0.0)
                except Exception:
                    working_hours_sec = tot_sec
            elif len(ts_list) == 1:
                working_hours_sec = max(tot_sec, 600.0)
            else:
                working_hours_sec = tot_sec

            rework_pct = round((rew_sec / tot_sec * 100.0), 1) if tot_sec > 0 else 0.0

            rows.append({
                "User": user,
                "Total Tasks": tasks_cnt,
                "Total Duration": tot_sec,
                "New Videos (First Time)": new_sec,
                "Reworks": rew_sec,
                "New Tasks": st["First Time Tasks"],
                "Rework Tasks": st["Rework Tasks"],
                "Working Hours Seconds": working_hours_sec,
                "Working Hours": format_seconds_hms(working_hours_sec),
                "Rework Pct": rework_pct,
                "Comments / Reasons": "; ".join(st["Rework Reasons"]) if st["Rework Reasons"] else "-",
                "RawID": raw_id_str,
            })

        if batches_tracker_info:
            self.update_daily_tracker(target_date, batches_tracker_info)

        return pd.DataFrame(rows)


def get_batch_work_classifier(scraper: Optional[HTTPScraper] = None) -> BatchWorkClassifier:
    """Factory helper to get BatchWorkClassifier instance."""
    return BatchWorkClassifier(scraper=scraper)
