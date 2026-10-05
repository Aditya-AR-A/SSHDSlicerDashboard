"""Read-only, fully paginated primary API evidence for daily submissions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from slicing_dashboard.processing.daily_work import daily_submissions, instant, batch_key, EXCLUDED


class DailyWorkSource:
    def __init__(self, scraper, canonical_name=None):
        self.scraper = scraper
        self.canonical_name = canonical_name or (lambda uid, name: name)

    def request(self, path, params=None, body=None, headers=None):
        client = self.scraper._client
        url = self.scraper._base_url + path
        kwargs = {"timeout": 15.0}
        if body is not None:
            method = client.post
            kwargs["json"] = body
        else:
            method = client.get
            kwargs["params"] = params
        if headers:
            kwargs["headers"] = headers
        response = method(url, **kwargs)
        if response.status_code in (401, 403) and not headers:
            if not self.scraper.login():
                raise ConnectionError("Daily-work API session expired")
            response = method(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def fetch(self, target_date):
        if not self.scraper.is_authenticated and not self.scraper.login():
            raise ConnectionError("Daily-work API login failed")
        captured = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
        batches = self.request("/api/dashboard/batches", params={"start_date": target_date, "end_date": target_date, "workflow_type": "slice"})
        group = batches.get("scope", {}).get("group_id")
        body = {"page": 1, "page_size": 200}
        if group is not None:
            body["group_ids"] = [group]
        tasks, seen_cursors = {}, set()
        for _ in range(1000):
            payload = self.request("/api/tasks/overview", body=body)
            page = payload.get("items", [])
            meta = payload.get("meta", {})
            if not page and meta.get("has_more"):
                raise ValueError("Daily task pagination ended before all records were received")
            for task in page:
                tasks[task["id"]] = task
            if not meta.get("has_more"):
                break
            cursor = meta.get("next_cursor")
            if cursor:
                if cursor in seen_cursors:
                    raise ValueError("Daily task pagination repeated a cursor")
                seen_cursors.add(cursor)
                body["cursor"] = cursor
            else:
                body["page"] += 1
        else:
            raise ValueError("Daily task pagination did not finish")
        tasks = list(tasks.values())
        if tasks and not all("slice_submitted_at" in task for task in tasks):
            raise ValueError("Task overview is missing actual submission timestamps")
        submitted = daily_submissions(tasks, target_date)
        active = {(task.get("slicer_id"), task.get("slicer")) for task in submitted
                  if task.get("slicer_id") is not None and self.canonical_name(task.get("slicer_id"), task.get("slicer")) not in EXCLUDED}

        def returns(account):
            uid, username = account
            result = self.request("/api/requests/batch-return-history", params={"workflow_type": "slice", "limit": 100},
                                  headers={"x-user-id": str(uid)})
            return {"user_id": uid, "username": username, "data": result["data"]}

        with ThreadPoolExecutor(max_workers=5) as pool:
            returned = list(pool.map(returns, active))
        submission_times = {}
        for task in submitted:
            key = batch_key(task.get("slicer"), task.get("slice_batch"))
            submission_times.setdefault(key, []).append(instant(task["slice_submitted_at"]))
        need_reviews = set()
        for account in returned:
            for event in account["data"]:
                stamp = instant(event.get("returned_at"))
                times = submission_times.get(batch_key(account["username"], event.get("legacy_batch_number")), [])
                if stamp and stamp.date().isoformat() == target_date and any(t > stamp for t in times):
                    need_reviews.add(event["batch_id"])

        def reviews(batch_id):
            rows, cursor, seen = [], None, set()
            page = 1
            while True:
                params = {"workflow_type": "slice", "batch_id": batch_id, "page_size": 200, "page": page}
                if cursor:
                    params["cursor"] = cursor
                response = self.request("/api/review/reviewed-batches", params=params)
                rows.extend(response.get("data", []))
                if not response.get("meta", {}).get("has_more"):
                    return batch_id, {"data": rows}
                cursor = response["meta"].get("next_cursor")
                if cursor and cursor in seen:
                    raise ValueError("Batch review pagination repeated a cursor")
                seen.add(cursor)
                page += 1

        with ThreadPoolExecutor(max_workers=5) as pool:
            review_history = dict(pool.map(reviews, need_reviews))
        requests, page = [], 1
        while True:
            response = self.request("/api/requests", params={"page": page, "page_size": 200})
            items = response.get("data", [])
            requests.extend(items)
            total = response.get("meta", {}).get("total")
            if (total is not None and len(requests) >= total) or (total is None and len(items) < 200):
                break
            if not items:
                raise ValueError("Individual return-notice pagination ended early")
            page += 1
        return {"target_date": target_date, "captured_at": captured, "tasks": tasks,
                "returned_accounts": returned, "reviews": review_history, "assigned_batches": batches, "requests": requests}
