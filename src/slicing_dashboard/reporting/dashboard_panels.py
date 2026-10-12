"""Compact, scoped chart captures. Reading a saved panel never contacts a source."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock

import pandas as pd

from slicing_dashboard.management.periods import today_iso
from slicing_dashboard.processing.daily_work import EXCLUDED

KINDS = ('summary', 'completion', 'rework', 'assigned', 'pending')
VERSION = 1
_LOCK = RLock()
FRAME_COLUMNS = {
    'completion': ('User', 'Completed Duration'),
    'pending': ('User', 'Stage', 'Duration', 'Count', 'IDs'),
    'assigned': ('User', 'Stage', 'Duration', 'Count', 'IDs', 'ID', 'AssignedDate', 'DaysAssigned',
                 'AssignmentDateBasis'),
    'rework': ('User', 'Total Batches', 'No Rework', '1 Rework', '2 Reworks', '3 Reworks',
               '4 Reworks', '5+ Reworks', '5+ Return Count', 'Reworked Once', 'Reworked Twice+',
               'Total Duration (hrs)', 'Avg Batch Duration (hrs)', 'Tier IDs'),
}
SUM_FIELDS = ('total_approved_duration', 'prev_approved_duration', 'total_error_duration',
              'prev_error_duration', 'rework_duration', 'prev_rework_duration', 'total_tasks',
              'completed_tasks', 'prev_completed_tasks', 'total_pending_duration',
              'leader_review_duration', 'auditor_review_duration', 'admin_review_duration')


def panel_key(manager, kind, start, end):
    if kind not in KINDS:
        raise ValueError('Unknown dashboard panel')
    scope = [today_iso()] if kind in ('pending', 'assigned') else [start, end]
    identity = [manager.scraper._base_url, manager.settings.admin_username]
    mapping = [manager.user_mapping, getattr(manager, 'user_mapping_full', {}),
               sorted(getattr(manager, 'exempt_ids', set()))]
    return sha256(json.dumps([VERSION, kind, scope, identity, mapping], sort_keys=True).encode()).hexdigest()


def saved_panel(manager, kind, start, end):
    key = panel_key(manager, kind, start, end)
    with _LOCK:
        return deepcopy(manager._snapshot_payload.get('dashboard_panels', {}).get(key))


def attach_panels(manager, payload):
    """Publish a snapshot without losing captures finishing during its assembly.

    The disk/database writer owns an immutable copy; interactive captures can
    keep updating the manager while that slower write runs.
    """
    with _LOCK:
        panels = deepcopy(manager._snapshot_payload.get('dashboard_panels', {}))
        payload['dashboard_panels'] = panels
        manager._snapshot_payload = {**payload, 'dashboard_panels': dict(panels)}


def frame_payload(frame, kind):
    if any(frame.attrs.get(key) for key in ('chart_error', 'pending_error', 'assignment_errors')):
        raise ValueError('Chart source unavailable')
    from slicing_dashboard.plots.guardrails import checked_frame
    required = (['User', 'Completed Duration'] if kind == 'completion' else
                ['User', 'Total Batches'] if kind == 'rework' else ['User', 'Stage', 'Duration'])
    labels = {'User', 'Stage', 'IDs', 'ID', 'AssignedDate', 'AssignmentDateBasis', 'Tier IDs'}
    columns = [column for column in FRAME_COLUMNS[kind] if column in frame]
    frame = checked_frame(frame, required, [column for column in columns if column not in labels])
    frame = frame[columns]
    # Preserve IDs needed by stack tooltips; omit task-level audit evidence.
    return {'columns': list(frame.columns), 'records': json.loads(frame.to_json(orient='records'))}


def panel_frame(packet):
    data = packet['data']
    return pd.DataFrame(data['records'], columns=data['columns'])


def selected_summary(data, users, roster):
    result = dict(data['all'])
    if users is None or set(users) == set(roster):
        return result
    for field in SUM_FIELDS:
        result[field] = sum(data['users'].get(user, {}).get(field, 0) for user in users)
    for label, current, previous in (
        ('approved', 'total_approved_duration', 'prev_approved_duration'),
        ('error', 'total_error_duration', 'prev_error_duration'),
        ('rework', 'rework_duration', 'prev_rework_duration'),
        ('completed', 'completed_tasks', 'prev_completed_tasks'),
    ):
        result[label + '_pct'] = ((result[current] - result[previous]) / result[previous] * 100
                                  if result[previous] else 0.0)
    return result


def capture_panel(manager, kind, start, end, force=False):
    """Read just one chart's sources; failures preserve its last successful capture."""
    started = datetime.now(timezone.utc).isoformat()
    try:
        with manager.refresh_scope(persist_snapshot=False):
            key = panel_key(manager, kind, start, end)
            if kind == 'summary':
                raw = manager.fetch_dashboard_data(start, end, force)
                all_kpis = manager.get_summary_kpis(start, end, force_refresh=force)
                if raw.get('_source_error') or all_kpis.get('_source_error'):
                    raise ValueError('Summary unavailable')
                users = set(manager.user_mapping.values()) | set(manager._snapshot_payload.get('available_users', []))
                by_user = {user: manager.get_summary_kpis(start, end, selected_users=[user])
                           for user in sorted(user for user in users if user and user not in EXCLUDED)}
                if any(row.get('_source_error') for row in by_user.values()):
                    raise ValueError('User summary unavailable')
                data = {'all': all_kpis, 'users': by_user,
                        'funnel': {row['key']: row for row in raw.get('breakdowns', {}).get('slice_funnel', [])}}
            elif kind == 'completion':
                data = frame_payload(manager.get_user_breakdown_df(start, end, force), kind)
            elif kind == 'rework':
                data = frame_payload(manager.get_batch_rework_ratio_df(start, end, force_refresh=force), kind)
            elif kind == 'pending':
                data = frame_payload(manager.get_pending_review_df(force_refresh=force), kind)
            elif kind == 'assigned':
                manager.sync_batches_master(start, end, force_refresh=force)
                frame = manager.get_detailed_pending_assigned_df(
                    start_date='2020-01-01', end_date=today_iso(),
                    force_refresh=force, include_pending=False)
                if not frame.empty:
                    frame = frame[frame['Stage'].isin(['New Assigned', 'Rework Assigned'])]
                data = frame_payload(frame, kind)
            else:
                raise ValueError('Unknown dashboard panel')
            if key != panel_key(manager, kind, start, end):
                raise ValueError('Chart mapping or scope changed during capture')
        packet = {'available': True, 'data': data, 'started_at': started,
                  'captured_at': datetime.now(timezone.utc).isoformat()}
        with _LOCK:
            panels = manager._snapshot_payload.setdefault('dashboard_panels', {})
            if panels.get(key, {}).get('started_at', '') <= started:
                panels[key] = deepcopy(packet)
                # Bound snapshot growth when users explore many custom periods.
                for old in sorted(panels, key=lambda item: panels[item]['started_at'])[:-60]:
                    del panels[old]
        return packet
    except Exception as error:
        return {'available': False, 'error': type(error).__name__, 'started_at': started}
