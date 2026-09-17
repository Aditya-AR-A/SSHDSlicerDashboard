#!/usr/bin/env python3
"""
Fetch and display Slice Data Dashboard efficiency metrics directly from the API.

Matches the upstream dashboard:
- GET /api/dashboard/annotator-efficiency-overall
- GET /api/dashboard/annotator-efficiency
"""
import argparse
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd
from rich.console import Console
from rich.table import Table

from slicing_dashboard.config import get_settings


def format_seconds(seconds: Optional[float]) -> str:
    """Format seconds into HH:MM:SS, supporting >= 100 hours."""
    if seconds is None or pd.isna(seconds):
        return "00:00:00"
    sec = int(round(float(seconds)))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_count_dur(count: Optional[int], duration: Optional[float]) -> str:
    """Format count and duration as '<count> / <HH:MM:SS>'."""
    c = int(count or 0)
    d_str = format_seconds(duration)
    return f"{c} / {d_str}"


def build_row(username: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a dictionary row conforming to the upstream table schema."""
    return {
        "Username": username,
        "Total Count": int(data.get("total_count", 0) or 0),
        "Total Duration": format_seconds(data.get("total_duration_seconds", 0)),
        "Completed Slice Count/Duration": format_count_dur(
            data.get("completed_count", 0), data.get("completed_duration_seconds", 0)
        ),
        "Submitted Slice Count/Duration": format_count_dur(
            data.get("submitted_count", 0), data.get("submitted_duration_seconds", 0)
        ),
        "Pending Leader Review Count/Duration": format_count_dur(
            data.get("leader_review_count", 0), data.get("leader_review_duration_seconds", 0)
        ),
        "Pending Auditor Review Count/Duration": format_count_dur(
            data.get("auditor_review_count", 0), data.get("auditor_review_duration_seconds", 0)
        ),
        "Pending Admin Review Count/Duration": format_count_dur(
            data.get("admin_review_count", 0), data.get("admin_review_duration_seconds", 0)
        ),
        "Error Video Pending Review Count/Duration": format_count_dur(
            data.get("error_review_count", 0), data.get("error_review_duration_seconds", 0)
        ),
        "Rework Slice Count/Duration": format_count_dur(
            data.get("rework_count", 0), data.get("rework_duration_seconds", 0)
        ),
    }


def fetch_slice_dashboard_data(
    start_date: str,
    end_date: str,
    role: int = 2,
    base_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> pd.DataFrame:
    """Authenticate and fetch slice data overview table from the API."""
    settings = get_settings()
    url = (base_url or settings.dashboard_url).rstrip("/")
    user = username or settings.admin_username
    pwd = password or settings.admin_password

    client = httpx.Client(timeout=30.0)

    # 1. Login
    login_resp = client.post(
        f"{url}/api/auth/login",
        json={"account": user, "password": pwd},
    )
    login_resp.raise_for_status()

    # 2. Fetch overall summary (Row: All Slicers)
    overall_resp = client.get(
        f"{url}/api/dashboard/annotator-efficiency-overall",
        params={"start_date": start_date, "end_date": end_date, "role": role},
    )
    overall_resp.raise_for_status()
    summary_data = overall_resp.json().get("summary", {})

    # 3. Fetch user items (Paginated, up to 200 items per page)
    items: List[Dict[str, Any]] = []
    page = 1
    while True:
        eff_resp = client.get(
            f"{url}/api/dashboard/annotator-efficiency",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "role": role,
                "page": page,
                "page_size": 200,
            },
        )
        eff_resp.raise_for_status()
        page_json = eff_resp.json()
        page_items = page_json.get("items", [])
        if not page_items:
            break
        items.extend(page_items)
        total = page_json.get("total", len(items))
        if len(items) >= total or len(page_items) < 200:
            break
        page += 1

    rows = []
    # Add summary row at the top
    summary_label = "All Slicers" if role == 2 else "All Annotators"
    rows.append(build_row(summary_label, summary_data))

    # Add each user row
    for item in items:
        uname = item.get("username", f"user-{item.get('user_id')}")
        rows.append(build_row(uname, item))

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display Slice Data Dashboard directly from API."
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2026-09-01",
        help="Start date YYYY-MM-DD (default: 2026-09-01)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default="2026-09-17",
        help="End date YYYY-MM-DD (default: 2026-09-17)",
    )
    parser.add_argument(
        "--role",
        type=int,
        default=2,
        help="Role ID: 2 for Slicers, 3 for Annotators (default: 2)",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["table", "tsv", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        help="Optional path to export to CSV or Excel (.csv / .xlsx)",
    )

    args = parser.parse_args()

    try:
        df = fetch_slice_dashboard_data(
            start_date=args.start_date,
            end_date=args.end_date,
            role=args.role,
        )
    except Exception as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        sys.exit(1)

    # Number of users excluding the summary row
    num_users = len(df) - 1
    print(f"Slice Data Dashboard ({args.start_date} to {args.end_date})")
    print(f"Users: {num_users}\n")

    if args.format == "table":
        console = Console()
        table = Table(title=f"Slice Data Dashboard ({args.start_date} to {args.end_date})", show_header=True, header_style="bold cyan")
        for col in df.columns:
            table.add_column(col, justify="left" if col == "Username" else "right")
        for idx, row in df.iterrows():
            style = "bold yellow" if idx == 0 else None
            table.add_row(*[str(val) for val in row], style=style)
        console.print(table)
    elif args.format == "tsv":
        print(df.to_csv(sep="\t", index=False))
    elif args.format == "csv":
        print(df.to_csv(index=False))
    elif args.format == "json":
        print(df.to_json(orient="records", indent=2))

    if args.export:
        if args.export.endswith(".xlsx"):
            df.to_excel(args.export, index=False)
            print(f"\nExported to Excel: {args.export}")
        else:
            df.to_csv(args.export, index=False)
            print(f"\nExported to CSV: {args.export}")


if __name__ == "__main__":
    main()
