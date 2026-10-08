"""Read models for the shared workflow inbox and paginated history."""
from urllib.parse import urlencode

from slicing_dashboard.processing.daily_work import instant


def local_time(value):
    stamp = instant(value)
    return stamp.strftime('%Y-%m-%d %H:%M:%S') if stamp else 'Unknown'


def notice_link(notice):
    reference = {'batch': notice['batch_id']} if notice.get('batch_id') else {'task': notice.get('task_id') or notice.get('task_alias') or ''}
    return '/workflow?' + urlencode({**reference, 'event': notice['event_id']})


def history_page(history, filters=None, page=1, page_size=25):
    rows, total = history.store.query('event', history.instance, filters, page, page_size)
    return {'rows': rows, 'total': total, 'page': page, 'page_size': page_size,
            'storage': history.store.storage_label}


def event_table_rows(rows):
    return [{
        'Event ID': row['id'], 'Event Time (India)': local_time(row.get('event_at')),
        'Recorded (India)': local_time(row.get('observed_at')), 'Member': row.get('member') or 'Unresolved',
        'Batch': row.get('batch_id') or 'Unlinked', 'Task': row.get('task_id') or row.get('task_alias') or '',
        'Action': row.get('action'), 'Result': row.get('result'), 'Stage': row.get('stage') or 'Unresolved',
        'Updated By': row.get('actor') if row.get('actor_id') is not None else 'Identity unavailable',
        'Actor Category': row.get('actor_type') or 'Unresolved',
        'Evidence': row.get('provenance'), 'Cycle': row.get('cycle'), 'Reason': row.get('reason') or '',
        'Source Status': row.get('source_status') or '',
    } for row in rows]
