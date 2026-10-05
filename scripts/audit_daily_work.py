"""Capture read-only API evidence for the daily-work audit."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from slicing_dashboard.scraper.http_scraper import HTTPScraper
from slicing_dashboard.management.periods import today_iso


def main():
    scraper = HTTPScraper()
    if not scraper.login():
        raise RuntimeError("API login failed")
    date = today_iso()
    output = Path("data/reports") / f"daily-audit-{date}"
    output.mkdir(parents=True, exist_ok=True)
    queries = {
        "efficiency": ("/api/dashboard/annotator-efficiency", {"start_date": date, "end_date": date, "role": 2, "page_size": 200}),
        "overall": ("/api/dashboard/annotator-efficiency-overall", {"start_date": date, "end_date": date, "role": 2}),
        "batches": ("/api/dashboard/batches", {"start_date": date, "end_date": date, "workflow_type": "slice"}),
        "tasks": ("/api/slice/tasks", {"page": 1, "page_size": 200}),
        "reviewed_tasks": ("/api/review/reviewed-tasks", {"page": 1, "page_size": 200}),
        "requests": ("/api/requests", {"page": 1, "page_size": 200}),
        "openapi": ("/openapi.json", {}),
    }
    def fetch(query):
        name, (path, params) = query
        response = scraper._client.get(scraper._base_url + path, params=params, timeout=20)
        if response.status_code != 200:
            return name, {"http_status": response.status_code}
        return name, response.json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, payload in pool.map(fetch, queries.items()):
            (output / (name + ".json")).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            print(name, "keys:", list(payload) if isinstance(payload, dict) else type(payload).__name__, flush=True)
    print("Saved audit evidence:", output, flush=True)
    scraper.close()


if __name__ == "__main__":
    main()
