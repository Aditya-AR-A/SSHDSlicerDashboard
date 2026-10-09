"""Dated source-status captures, separate from submission and approval events.

The upstream day is Asia/Shanghai. A completed or pending duration describes
the source's date-filtered current status, never hours approved on that date.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import math

from slicing_dashboard.processing.workflow_history import WorkflowStore
from slicing_dashboard.processing.source_policy import serialized_source, SOURCE_TTL_SECONDS
from slicing_dashboard.reporting.dashboard_reports import reporting_user


METRICS = ('total', 'completed', 'submitted', 'leader_review', 'auditor_review',
           'admin_review', 'error_review', 'rework')
FIELDS = tuple(field for metric in METRICS
               for field in (metric + '_count', metric + '_duration_seconds')) + ('work_duration_seconds',)


def validated_items(items):
    if not isinstance(items, list):
        raise ValueError('Missing efficiency items')
    result, identities = [], set()
    for item in items:
        if not isinstance(item, dict) or item.get('user_id') is None or not item.get('username'):
            raise ValueError('Missing efficiency account')
        identity = str(item['user_id'])
        if identity in identities:
            raise ValueError('Repeated efficiency account')
        identities.add(identity)
        row = {'user_id': item['user_id'], 'username': item['username']}
        for field in FIELDS:
            value = item.get(field)
            if isinstance(value, bool) or value is None:
                raise ValueError('Missing efficiency metric')
            value = float(value)
            if not math.isfinite(value) or value < 0 or (field.endswith('_count') and not value.is_integer()):
                raise ValueError('Invalid efficiency metric')
            row[field] = int(value) if field.endswith('_count') else value
        result.append(row)
    return result


class EfficiencyHistory:
    def __init__(self, manager, store=None):
        self.manager = manager
        self.store = store or WorkflowStore(manager.db)
        self.leader_id = manager.settings.efficiency_leader_id
        scope = f'{manager.scraper._base_url.rstrip("/")}:{self.leader_id}:2'
        self.instance = 'efficiency:' + hashlib.sha256(scope.encode()).hexdigest()[:24]

    def key(self, day):
        return f'{self.instance}:{day}'

    def capture(self, day, force=False):
        return _capture(self.manager, self, day, force)

    def capture_range(self, start, end, force=False):
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or last > datetime.now(timezone(timedelta(hours=8))).date():
            raise ValueError('Invalid efficiency date range')
        days = [(first + timedelta(days=offset)).isoformat()
                for offset in range((last - first).days + 1)]
        # Each successful day is committed before moving on. Retrying resumes
        # retained days rather than replacing a shared dashboard snapshot.
        return [self.capture(day, force=force) for day in days]

    def read(self, start, end):
        rows = []
        for record in self.store.records('efficiency-capture', self.instance, start, end):
            if not start <= record['date'] <= end:
                continue
            totals = {}
            for item in validated_items(record['items']):
                user = self.manager._get_reporting_name(item['user_id'], item['username'])
                if not reporting_user(user) or user == 'Exempt':
                    continue
                row = totals.setdefault(user, {'User': user, **{field: 0 for field in FIELDS}})
                for field in FIELDS:
                    row[field] += item[field]
            rows.append({'date': record['date'], 'rows': list(totals.values()),
                         'metadata': {'available': True, 'source': 'annotator-efficiency',
                                      'timezone': 'Asia/Shanghai', 'captured_at': record['captured_at'],
                                      'date_basis': 'Source-date current status'}})
        return sorted(rows, key=lambda row: row['date'])

    def read_completed(self, start, end):
        rows = []
        for record in self.store.chart_records('efficiency-capture', self.instance, start, end):
            if not start <= record['date'] <= end:
                continue
            totals = {}
            for item in record['items']:
                user = self.manager._get_reporting_name(item['user_id'], item['username'])
                if not reporting_user(user):
                    continue
                seconds = float(item['completed_duration_seconds'])
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError('Invalid completion duration')
                row = totals.setdefault(user, {'User': user, 'completed_duration_seconds': 0})
                row['completed_duration_seconds'] += seconds
            rows.append({'date': record['date'], 'rows': list(totals.values()),
                         'metadata': {'available': True, 'source': 'annotator-efficiency',
                                      'timezone': 'Asia/Shanghai', 'captured_at': record['captured_at'],
                                      'date_basis': 'Source-date current status'}})
        return sorted(rows, key=lambda row: row['date'])


@serialized_source
def _capture(manager, history, target_date, force=False):
    date.fromisoformat(target_date)
    saved = history.store.get(history.key(target_date))
    now = datetime.now(timezone.utc)
    if saved and not force:
        age = (now - datetime.fromisoformat(saved['captured_at'])).total_seconds()
        if target_date < now.astimezone(timezone(timedelta(hours=8))).date().isoformat() or 0 <= age < SOURCE_TTL_SECONDS:
            return saved
    if not manager.scraper.is_authenticated and not manager.scraper.login():
        raise ConnectionError('Efficiency login failed')
    began = now.isoformat()
    items, expected = [], None
    for page in range(1, 101):
        response = manager.scraper._client.post(
            manager.scraper._base_url + '/api/dashboard/annotator-efficiency',
            json={'start_date': target_date, 'end_date': target_date, 'role': 2,
                  'leader_id': history.leader_id, 'page': page, 'page_size': 200}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('Invalid efficiency response')
        source_range = payload.get('date_range')
        if source_range != {'start_date': target_date, 'end_date': target_date}:
            raise ValueError('Efficiency date range mismatch')
        total = payload.get('total')
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError('Missing efficiency pagination total')
        if expected is not None and total != expected:
            raise ValueError('Efficiency inventory changed')
        expected = total
        items.extend(payload['items'])
        if len(items) > expected or (not payload['items'] and len(items) != expected):
            raise ValueError('Incomplete efficiency pagination')
        if len(items) == expected:
            record = {'id': history.key(target_date), 'instance': history.instance,
                      'date': target_date, 'display_date': target_date, 'role': 2,
                      'leader_id': history.leader_id, 'timezone': 'Asia/Shanghai',
                      'source': '/api/dashboard/annotator-efficiency',
                      'sort_at': began, 'captured_at': datetime.now(timezone.utc).isoformat(),
                      'items': validated_items(items)}
            if not history.store.save_newer(record['id'], 'efficiency-capture', record):
                return history.store.get(record['id'])
            return record
    raise ValueError('Efficiency pagination limit reached')
