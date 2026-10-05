"""Read-only, fully paginated primary API evidence for daily submissions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import RLock
from zoneinfo import ZoneInfo

from slicing_dashboard.processing.daily_work import daily_submissions, instant, batch_key, EXCLUDED


class DailyWorkSource:
    """Evidence shared by daily views during one explicit dashboard refresh.

    A caller must discard or invalidate this instance at the end of its refresh
    scope. Reusing a single inventory for Today and Yesterday saves a second
    full scan without allowing a later Refresh to use old submissions.
    """

    TASK_PAGE_SIZE = 500  # The live API's interactive overview maximum.
    INVENTORY_WORKERS = 4
    TASK_FIELDS = ("id", "slicer_id", "slicer", "slice_batch", "slice_submitted_at", "duration_seconds")

    def __init__(self, scraper, canonical_name=None, group_id=None):
        self.scraper = scraper
        self.canonical_name = canonical_name or (lambda uid, name: name)
        self.group_id = group_id
        self._lock = RLock()
        self.invalidate()

    def invalidate(self):
        """Start a new evidence capture; never reuse it across manual refreshes."""
        with self._lock:
            self._base = None
            self._returns = {}
            self._reviews = {}

    def seed_returns(self, responses):
        """Reuse successful member responses captured in this refresh scope.

        ``responses`` maps member IDs to raw batch-return-history payloads.
        Persisted returns or failed requests must not be seeded by the caller.
        """
        with self._lock:
            for user_id, payload in responses.items():
                if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    self._returns[user_id] = payload["data"]

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

    def _inventory(self, target_date):
        group = self.group_id
        if group is None:
            batches = self.request("/api/dashboard/batches", params={"start_date": target_date, "end_date": target_date, "workflow_type": "slice"})
            scope = batches.get("scope", {})
            group = scope.get("group_id")
        else:
            scope = {"group_id": group}
        body = {"page": 1, "page_size": self.TASK_PAGE_SIZE}
        if group is not None:
            body["group_ids"] = [group]
        first = self.request("/api/tasks/overview", body=body)
        if self._supports_offsets(first):
            try:
                return self._parallel_inventory(body, first), {"scope": scope}
            except Exception:
                # A changing inventory, duplicate boundary, or unsupported
                # offset invalidates the whole attempted capture. Start a new
                # cursor chain rather than exposing any partial offset data.
                return self._cursor_inventory(body), {"scope": scope}
        return self._cursor_inventory(body, first), {"scope": scope}

    def _supports_offsets(self, first):
        total, meta = first.get("total"), first.get("meta", {})
        limit = meta.get("max_offset_rows")
        return (type(total) is int and total >= 0 and meta.get("total_capped") is False
                and type(limit) is int and total <= min(limit, 10000))

    def _minimal_task(self, task):
        if "slice_submitted_at" not in task:
            raise ValueError("Task overview is missing actual submission timestamps")
        # Daily evidence needs six fields, not annotation/review/media
        # metadata for every task in the group's entire history.
        return {field: task.get(field) for field in self.TASK_FIELDS}

    def _parallel_inventory(self, body, first):
        total, size = first["total"], body["page_size"]
        pages = max(1, (total + size - 1) // size)
        tasks = {}

        def collect(number, payload):
            meta, rows = payload.get("meta", {}), payload.get("items", [])
            expected = min(size, max(0, total - (number - 1) * size))
            if (payload.get("total") != total or meta.get("total_capped") is not False
                    or meta.get("page") != number or meta.get("page_size") != size
                    or len(rows) != expected or meta.get("has_more") is not (number < pages)):
                raise ValueError("Task inventory changed during offset pagination")
            for task in rows:
                if task["id"] in tasks:
                    raise ValueError("Task offset pagination repeated a task")
                tasks[task["id"]] = self._minimal_task(task)

        collect(1, first)

        def fetch_page(number):
            return number, self.request("/api/tasks/overview", body={**body, "page": number})

        with ThreadPoolExecutor(max_workers=self.INVENTORY_WORKERS) as pool:
            for number, payload in pool.map(fetch_page, range(2, pages + 1)):
                collect(number, payload)
        if len(tasks) != total:
            raise ValueError("Task offset pagination did not capture the complete inventory")
        return list(tasks.values())

    def _cursor_inventory(self, body, first=None):
        body = dict(body)
        tasks, seen_cursors = {}, set()
        for index in range(1000):
            payload = first if index == 0 and first is not None else self.request("/api/tasks/overview", body=body)
            page = payload.get("items", [])
            meta = payload.get("meta", {})
            if not page and meta.get("has_more"):
                raise ValueError("Daily task pagination ended before all records were received")
            for task in page:
                tasks[task["id"]] = self._minimal_task(task)
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
        return list(tasks.values())

    def _individual_returns(self):
        requests, page, received = [], 1, 0
        while True:
            response = self.request("/api/requests", params={"page": page, "page_size": 200})
            items = response.get("data", [])
            for item in items:
                kind = str(item.get("request_type") or "").lower()
                if item.get("workflow_type") == "slice" and any(term in kind for term in ("rework", "return", "reject")):
                    requests.append({key: item.get(key) for key in ("id", "task_id", "workflow_type", "request_type", "created_at")})
            total = response.get("meta", {}).get("total")
            # Count all fetched notices, including unrelated request types.
            received += len(items)
            if (total is not None and received >= total) or (total is None and len(items) < 200):
                return requests
            if not items:
                raise ValueError("Individual return-notice pagination ended early")
            page += 1

    def prepare(self, target_date):
        """Capture inventory and notices while another panel reads returns.

        This deliberately does not load member return histories or reviews.
        The caller can seed successful returns before ``fetch`` completes the
        classification, avoiding a second set of history requests.
        """
        with self._lock:
            return self._prepare(target_date)

    def _prepare(self, target_date):
        if not self.scraper.is_authenticated and not self.scraper.login():
            raise ConnectionError("Daily-work API login failed")
        if self._base is None:
            captured = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            # Notices are independent of the inventory's cursor chain.
            with ThreadPoolExecutor(max_workers=2) as pool:
                inventory = pool.submit(self._inventory, target_date)
                individual = pool.submit(self._individual_returns)
                tasks, batches = inventory.result()
                requests = individual.result()
            self._base = {"captured_at": captured, "tasks": tasks,
                          "assigned_batches": batches, "requests": requests}
        return self._base

    def fetch(self, target_date):
        # Serialize captures to prevent duplicate scans when daily panels
        # request evidence concurrently in the same refresh scope.
        with self._lock:
            return self._fetch(target_date)

    def _fetch(self, target_date):
        self._prepare(target_date)
        tasks = self._base["tasks"]
        submitted = daily_submissions(tasks, target_date)
        active = {(task.get("slicer_id"), task.get("slicer")) for task in submitted
                  if task.get("slicer_id") is not None and self.canonical_name(task.get("slicer_id"), task.get("slicer")) not in EXCLUDED}

        def returns(account):
            uid, username = account
            result = self.request("/api/requests/batch-return-history", params={"workflow_type": "slice", "limit": 100},
                                  headers={"x-user-id": str(uid)})
            return {"user_id": uid, "username": username, "data": result["data"]}

        with ThreadPoolExecutor(max_workers=5) as pool:
            for result in pool.map(returns, [account for account in active if account[0] not in self._returns]):
                self._returns[result["user_id"]] = result["data"]
        returned = [{"user_id": uid, "username": username, "data": self._returns[uid]}
                    for uid, username in active]
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
            self._reviews.update(pool.map(reviews, need_reviews - self._reviews.keys()))
        review_history = {batch: self._reviews[batch] for batch in need_reviews}
        return {**self._base, "target_date": target_date,
                "returned_accounts": returned, "reviews": review_history}
