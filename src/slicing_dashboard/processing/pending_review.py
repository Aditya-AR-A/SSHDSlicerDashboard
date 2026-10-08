"""Current review inventory: complete captures, bounded reuse, no disk fallback."""
from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import Lock
from time import monotonic

import pandas as pd

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.processing.daily_work import UPSTREAM
from slicing_dashboard.processing.source_policy import SOURCE_TTL_SECONDS

STAGES = (('leader', 'Pending Leader'), ('auditor', 'Pending Auditor'), ('admin', 'Pending Admin'))
_INITIALIZE = Lock()


def current_review_end():
    # The API's calendar dates use the source timezone. At an India evening
    # boundary, newly submitted source-next-day tasks must still be included.
    return max(today_iso(), datetime.now(UPSTREAM).date().isoformat())


def number(value, *, count=False):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing review metric')
    result = float(value)
    if not isfinite(result) or result < 0 or (count and not result.is_integer()):
        raise ValueError('Invalid review metric')
    return int(result) if count else result


def fetch_items(scraper, end):
    """The aggregate endpoint avoids the capped task listing. No settlement filter."""
    if not scraper.is_authenticated and not scraper.login():
        raise ConnectionError('Review inventory login failed')
    items, identities, expected = [], set(), None
    for page in range(1, 101):
        response = scraper._client.get(
            f'{scraper._base_url}/api/dashboard/annotator-efficiency',
            params={'start_date': '2020-01-01', 'end_date': end, 'role': 2,
                    'page': page, 'page_size': 200},
            headers={'Cache-Control': 'no-cache'}, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('Invalid review response')
        rows = payload['items']
        if payload.get('total') is not None:
            total = number(payload['total'], count=True)
            if expected is not None and total != expected:
                raise ValueError('Review inventory changed during pagination')
            expected = total
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('username'), str) or not row['username'].strip():
                raise ValueError('Missing review account')
            uid = row.get('user_id')
            identity = ('id', str(uid)) if uid is not None else ('name', row['username'].strip().casefold())
            if identity in identities:
                raise ValueError('Duplicate review account or repeated page')
            identities.add(identity)
            for field, _ in STAGES:
                number(row.get(f'{field}_review_duration_seconds'))
                number(row.get(f'{field}_review_count'), count=True)
        items.extend(rows)
        if expected is not None and len(items) >= expected:
            if len(items) != expected:
                raise ValueError('Inconsistent review inventory total')
            return items
        if not rows or (expected is None and len(rows) < 200):
            if expected is not None and len(items) != expected:
                raise ValueError('Incomplete review inventory')
            return items
    raise ValueError('Review inventory pagination limit reached')


class PendingReviewCapture:
    """A newer request/invalidation wins even when an older HTTP read finishes last."""
    def __init__(self):
        self.lock = Lock()
        self.generation = 0
        self.saved = None
        self.inflight = None
        self.inflight_end = None

    def invalidate(self):
        with self.lock:
            self.generation += 1
            self.saved = None
            self.inflight = None
            self.inflight_end = None

    def read(self, scraper, force=False):
        end = current_review_end()
        with self.lock:
            if not force and self.saved and self.saved['end'] == end and monotonic() - self.saved['clock'] < SOURCE_TTL_SECONDS:
                return deepcopy(self.saved)
            if not force and self.inflight and self.inflight_end == end:
                future, owner = self.inflight, False
            else:
                self.generation += 1
                generation = self.generation
                future = self.inflight = Future()
                self.inflight_end = end
                self.saved = None
                owner = True
        if not owner:
            return deepcopy(future.result())
        try:
            result = {'items': fetch_items(scraper, end), 'end': end, 'clock': monotonic(),
                      'captured_at': datetime.now(timezone.utc).isoformat(), 'error': None}
        except Exception as error:
            result = {'items': [], 'end': end, 'captured_at': None, 'error': type(error).__name__}
        with self.lock:
            superseded = generation != self.generation
            if not superseded:
                self.inflight = None
                self.inflight_end = None
                if not result['error']:
                    self.saved = result
        if superseded:
            result = self.read(scraper)
        future.set_result(result)
        return deepcopy(result)


def capture_for(manager):
    with _INITIALIZE:
        if not hasattr(manager, '_pending_review_capture'):
            manager._pending_review_capture = PendingReviewCapture()
        return manager._pending_review_capture


def pending_frame(manager, force_refresh=False):
    capture = capture_for(manager).read(manager.scraper, force_refresh)
    totals = {}
    for row in capture['items']:
        user = manager._get_reporting_name(row.get('user_id'), row['username'])
        if not user or str(user).casefold() in {'admin', 'test', 'dep', 'exempt', 'user-none', '(unassigned)', 'unassigned'}:
            continue
        for field, stage in STAGES:
            duration = number(row[f'{field}_review_duration_seconds'])
            count = number(row[f'{field}_review_count'], count=True)
            # Include verified zeros; the chart can distinguish no queue from a failed read.
            entry = totals.setdefault((user, stage), {'User': user, 'Stage': stage, 'Duration': 0., 'Count': 0, 'IDs': set()})
            entry['Duration'] += duration
            entry['Count'] += count
            entry['IDs'].add(row['username'])
    rows = [{**row, 'IDs': ', '.join(sorted(row['IDs']))} for row in totals.values()]
    frame = pd.DataFrame(rows, columns=['User', 'Stage', 'Duration', 'Count', 'IDs'])
    frame.attrs.update(pending_error=capture['error'], captured_at=capture['captured_at'])
    return frame
