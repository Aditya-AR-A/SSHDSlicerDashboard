"""Settlement dates, evaluated in the dashboard's India time zone."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def today_iso():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def available_periods(saved, today=None):
    today = today or today_iso()
    periods = sorted((dict(p) for p in saved), key=lambda p: p["start_date"])
    for period in periods:
        period["is_current"] = False
    if not periods:
        periods.append({"start_date": "2026-09-01", "end_date": today, "settled": False})
    latest = periods[-1]
    if latest.get("settled"):
        start = (datetime.strptime(latest["end_date"], "%Y-%m-%d") + timedelta(days=1)).date().isoformat()
        if start <= today:
            periods.append({"start_date": start, "end_date": today, "settled": False})
    if not periods[-1].get("settled") and periods[-1]["start_date"] <= today:
        periods[-1].update(end_date=today, is_current=True)
    for period in periods:
        current = period["is_current"]
        status = "Current · through today" if current else "Settled" if period.get("settled") else "Unsettled"
        period["label"] = f"{period['start_date']} to {period['end_date']} · {status}"
        period["value"] = f"{period['start_date']}|current" if current else f"{period['start_date']}|{period['end_date']}"
    return periods


def validate_period_rows(rows, today=None):
    today = today or today_iso()
    if not rows:
        raise ValueError("Add at least one period before saving.")
    periods = []
    for index, row in enumerate(rows, 1):
        start = str(row.get("start_date") or "").strip()
        end = str(row.get("end_date") or "").strip()
        status = row.get("status", "Settled" if row.get("settled") in (True, "Yes") else "Unsettled")
        if status not in ("Current", "Settled", "Unsettled"):
            raise ValueError(f"Row {index}: choose a valid status.")
        if status == "Current":
            end = today
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Row {index}: enter both dates as YYYY-MM-DD.") from None
        if end_date < start_date:
            raise ValueError(f"Row {index}: end date must be on or after the start date.")
        if end > today:
            raise ValueError(f"Row {index}: the end date cannot be after today.")
        periods.append({"start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                        "settled": status == "Settled", "is_current": status == "Current"})
    periods.sort(key=lambda p: p["start_date"])
    if sum(p["is_current"] for p in periods) > 1:
        raise ValueError("Only one period can be Current.")
    for previous, period in zip(periods, periods[1:]):
        if period["start_date"] <= previous["end_date"]:
            raise ValueError("Periods overlap. Each period must start after the previous one ends.")
    if any(p["is_current"] for p in periods[:-1]):
        raise ValueError("The Current period must be the latest period.")
    if periods[-1]["settled"] is False:
        periods[-1].update(end_date=today, is_current=True)
    return periods
