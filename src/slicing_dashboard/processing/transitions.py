import pandas as pd
from datetime import datetime


def build_transitions(master_df: pd.DataFrame, reviewed_tasks: list[dict]
    ) ->pd.DataFrame:
    """Build a complete transition events DataFrame from master tasks and historical review logs."""
    events = []
    slicer_to_user = {}
    for _, row in master_df.iterrows():
        sid = row.get('slicer_id')
        uname = row.get('user_name')
        if pd.notna(sid) and pd.notna(uname):
            try:
                slicer_to_user[int(float(sid))] = uname
            except (ValueError, TypeError):
                pass
    for item in reviewed_tasks:
        task_id = item.get('id')
        slicer_id = item.get('slicer_id')
        user_name = 'unknown'
        if slicer_id is not None:
            try:
                user_name = slicer_to_user.get(int(float(slicer_id)),
                    f'user-{slicer_id}')
            except (ValueError, TypeError):
                user_name = f'user-{slicer_id}'
        start = item.get('start', 0.0)
        end = item.get('end', 0.0)
        try:
            duration_seconds = max(0.0, float(end) - float(start))
        except (ValueError, TypeError):
            duration_seconds = 0.0
        reviewed_at = item.get('reviewed_at')
        if not reviewed_at:
            continue
        try:
            date_str = reviewed_at[:10]
            datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            continue
        action = item.get('review_action')
        decision = item.get('review_decision')
        prev_status = item.get('review_previous_status')
        curr_status = item.get('review_current_status')
        if (action == 'CONFIRM_SLICE_LEADER' or decision == 'approved' and 
            curr_status == 'slice_pending_auditor_review'):
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'submitted'})
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'leader_passed'})
        elif (action == 'SLICE_REWORK' or decision == 'returned'
            ) and prev_status == 'slice_submitted':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'submitted'})
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'leader_returned'})
        elif (action == 'SLICE_REWORK' or decision == 'returned'
            ) and prev_status == 'slice_pending_auditor_review':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'auditor_returned'})
        elif action == 'REJECT_VIDEO_ERROR' or decision == 'error_rejected':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'auditor_returned'})
        elif action == 'CONFIRM_VIDEO_ERROR' or decision == 'error_confirmed':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'auditor_passed'})
    events_set = {(e['task_id'], e['type']) for e in events}
    
    for _, row in master_df.iterrows():
        task_id = row.get('id')
        user_name = row.get('user_name')
        status_norm = row.get('status_normalized')
        date_str = row.get('completed_date')
        duration_seconds = float(row.get('duration_seconds', 0.0))
        rework_by = row.get('rework_by')
        if pd.isna(user_name) or pd.isna(date_str) or pd.isna(status_norm):
            continue
        if status_norm == 'slice_submitted':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'submitted'})
            events_set.add((task_id, 'submitted'))
        elif status_norm == 'slice_pending_auditor_review':
            has_log = (task_id, 'leader_passed') in events_set
            if not has_log:
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': 'submitted'})
                events_set.add((task_id, 'submitted'))
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': 'leader_passed'})
                events_set.add((task_id, 'leader_passed'))
        elif status_norm == 'slice_pending_admin_review':
            has_log = (task_id, 'auditor_passed') in events_set
            if not has_log:
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': 'auditor_passed'})
                events_set.add((task_id, 'auditor_passed'))
        elif status_norm == 'slice_rework':
            has_log = (task_id, 'leader_returned') in events_set or (task_id, 'auditor_returned') in events_set
            if not has_log:
                rtype = ('leader_returned' if rework_by == 'leader' else
                    'auditor_returned')
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': rtype})
                events_set.add((task_id, rtype))
        elif status_norm == 'slice_completed':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'completed'})
            events_set.add((task_id, 'completed'))
            has_log = (task_id, 'auditor_passed') in events_set
            if not has_log:
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': 'auditor_passed'})
                events_set.add((task_id, 'auditor_passed'))
        elif status_norm == 'video_error_confirmed':
            events.append({'task_id': task_id, 'user_name': user_name,
                'duration_seconds': duration_seconds, 'date': date_str,
                'type': 'completed'})
            events_set.add((task_id, 'completed'))
            has_log = (task_id, 'auditor_passed') in events_set
            if not has_log:
                events.append({'task_id': task_id, 'user_name': user_name,
                    'duration_seconds': duration_seconds, 'date': date_str,
                    'type': 'auditor_passed'})
                events_set.add((task_id, 'auditor_passed'))
    return pd.DataFrame(events)
