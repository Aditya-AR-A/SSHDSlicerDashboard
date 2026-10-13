"""Local-only dashboard QA fixture. Never reads production credentials or storage."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import date, timedelta
from pathlib import Path
import sys
import time
import logging
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from flask import request
from tests.test_daily_report_page import application_namespace, report_fixture
from slicing_dashboard.data_manager import DataManager
from slicing_dashboard.processing.pending_review import pending_frame

namespace, manager = application_namespace()
app = namespace['app']
state = {'mode': 'normal', 'seconds': 3600, 'delay': 0, 'reads': 0}
names = ['Priya', 'Riya']


def people():
    if state['mode'] == 'empty':
        return []
    if state['mode'] == 'one':
        return ['Riya']
    if state['mode'] == 'many':
        return ['Very long username ' + str(i) + ' extended account name' for i in range(35)]
    return names


def seconds():
    return 0 if state['mode'] == 'zero' else 1e18 if state['mode'] == 'large' else state['seconds']


review_manager = DataManager.__new__(DataManager)
review_manager.scraper = MagicMock(is_authenticated=True)
review_manager.scraper._base_url = 'http://fixture.invalid'
review_manager._get_reporting_name = lambda uid, username: username


def api(*args, **kwargs):
    state['reads'] += 1
    time.sleep(state['delay'])
    if state['mode'] == 'failure':
        raise ConnectionError('Fixture failure')
    status = kwargs['params']['status']
    stage = {'slice_submitted': 'leader', 'slice_pending_auditor_review': 'auditor',
             'slice_pending_admin_review': 'admin'}[status]
    duration = seconds() if stage == 'leader' else state.get(stage + '_seconds', 0)
    rows = [dict(id=f'{stage}-{i}', slicer_id=i, slicer=name, status=status,
                 duration_seconds=duration) for i, name in enumerate(people())
            if stage == 'leader' or duration]
    result = MagicMock()
    result.json.return_value = {'data': rows, 'meta': {'has_more': False, 'total': len(rows)}}
    return result


review_manager.scraper._client.get.side_effect = api
manager.get_pending_review_df.side_effect = lambda force_refresh=False: pending_frame(review_manager, force_refresh)
manager.invalidate_pending_review.side_effect = review_manager.invalidate_pending_review
manager._refresh_worker.side_effect = lambda fn: fn
manager.get_workflow_data.return_value = {'inbox': {'rows': [], 'unread': 0}, 'members': names, 'events': 0, 'synthetic': 0, 'checkpoints': []}
def workflow(*args, **kwargs):
    time.sleep(state.get('workflow_delay', 0))
    return manager.get_workflow_data.return_value
manager.get_workflow_data.side_effect = workflow
manager.check_server_heartbeat.return_value = True


def dashboard(*args, **kwargs):
    time.sleep(state.get('dashboard_delay', 0))
    start = args[0] if args else kwargs.get('start_date')
    end = args[1] if len(args) > 1 else kwargs.get('end_date')
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start and end else 1
    range_seconds = seconds() * span if state.get('range_sensitive') else seconds()
    kpis = {key: 1 for key in ('total_approved_duration', 'approved_pct', 'prev_approved_duration',
        'total_pending_duration', 'leader_review_duration', 'auditor_review_duration', 'admin_review_duration',
        'rework_duration', 'rework_pct', 'prev_rework_duration', 'total_error_duration', 'error_pct',
        'prev_error_duration', 'completed_tasks', 'total_tasks', 'completed_pct', 'prev_completed_tasks')}
    if state['mode'] in ('zero', 'large'):
        kpis = {key: (0 if state['mode'] == 'zero' else 1e18) for key in kpis}
    kpis.update(assignable_duration=None if state['mode'] == 'failure' else 0, assignable_count=0,
                assignable_error='Fixture failure' if state['mode'] == 'failure' else None)
    complete = pd.DataFrame([{'User': user, 'Completed Duration': range_seconds} for user in people()])
    ratios = pd.DataFrame([{'User': user, 'Total Batches': span if state.get('range_sensitive') else 2,
                            'No Rework': max(0, span - 1) if state.get('range_sensitive') else 1,
                            '1 Rework': 1} for user in people()])
    assigned = pd.DataFrame([{'User': user, 'Stage': 'New Assigned', 'Duration': seconds(), 'Count': 1,
                             'ID': user, 'AssignedDate': '2026-10-06'} for user in people()])
    if state['mode'] == 'failure':
        for frame in (complete, ratios, assigned):
            frame.attrs['chart_error'] = 'Chart data unavailable. Refresh to retry.'
    raw = {'breakdowns': {'slice_funnel': [
        {'key': 'assigned', 'duration_seconds': seconds() * len(people())},
        {'key': 'rework', 'duration_seconds': 0},
        {'key': 'pending_assign', 'duration_seconds': 0},
    ]}}
    if state['mode'] == 'failure':
        raw['_source_error'] = 'Fixture failure'
    return raw, kpis, complete, ratios, assigned


namespace['_dashboard_sources'] = dashboard

# Exercise the actual saved-first panel pipeline without production sources.
from types import SimpleNamespace
from slicing_dashboard.reporting.dashboard_panels import KINDS, capture_panel
manager.settings = SimpleNamespace(admin_username='fixture')
manager.scraper._base_url = 'https://fixture.invalid'
manager.user_mapping_full = {}
manager.exempt_ids = set()
manager.refresh_scope.side_effect = lambda **kwargs: nullcontext()
manager.fetch_dashboard_data.side_effect = lambda *args, **kwargs: dashboard(*args, **kwargs)[0]
manager.get_summary_kpis.side_effect = lambda *args, **kwargs: dashboard(*args, **kwargs)[1]
manager.get_user_breakdown_df.side_effect = lambda *args, **kwargs: dashboard(*args, **kwargs)[2]
manager.get_detailed_pending_assigned_df.side_effect = lambda *args, **kwargs: dashboard()[4]


def rework_panel(*args, **kwargs):
    time.sleep(state.get('rework_delay', 0))
    return dashboard(*args, **kwargs)[3]


manager.get_batch_rework_ratio_df.side_effect = rework_panel


def seed_panels():
    for kind in KINDS:
        capture_panel(manager, kind, '2026-10-01', '2026-10-06')


seed_panels()

# Include an older settlement so browser checks can exercise the real dropdown.
from tests.test_daily_report_page import components
manager.get_available_periods.return_value.append({
    'start_date': '2026-09-01', 'end_date': '2026-09-30', 'is_current': False,
    'value': '2026-09-01|2026-09-30', 'label': 'September 2026'})
for component in components(app.layout):
    if getattr(component, 'id', None) == 'settlement-dropdown':
        component.options.append({'label': 'September 2026', 'value': '2026-09-01|2026-09-30'})


def report(*args, **kwargs):
    rows = [{'User': user, 'Total Tasks': 2, 'Total Duration': seconds(), 'New Videos (First Time)': seconds(),
             'Same-day Rework': 0, 'Old Rework': 0, 'New Tasks': 2, 'Same-day Rework Tasks': 0,
             'Old Rework Tasks': 0, 'RawID': user} for user in people()]
    result = report_fixture(rows=rows, available=state['mode'] != 'failure')
    result['users'] = people() or names
    for day in result['history']:
        day['rows'] = deepcopy(rows)
        day['metadata']['available'] = state['mode'] != 'failure'
    result['days'] = {day['date']: day for day in result['history']}
    result['current_period'] = {'start_date': '2026-10-01', 'end_date': '2026-10-06', 'is_current': True}
    return result


def saved_daily(*args, **kwargs):
    return {**report(*args, **kwargs), 'saved_only': True, 'refresh_pending': True}


def live_daily(*args, **kwargs):
    time.sleep(state.get('daily_live_delay', 0))
    if state.get('daily_live_failure'):
        raise ConnectionError('Fixture live capture failure')
    result = report(*args, **kwargs)
    for day in result['history'][-2:]:
        for row in day['rows']:
            row['New Videos (First Time)'] *= 2
            row['Total Duration'] *= 2
    return result


manager.get_saved_daily_report_data.side_effect = saved_daily
manager.get_daily_report_data.side_effect = live_daily
manager.get_user_report_data.side_effect = report
def trend_report(report_date=None, force_refresh=False, start_date=None):
    result = report()
    if state.get('range_sensitive') and start_date and report_date:
        start, end = date.fromisoformat(start_date), date.fromisoformat(report_date)
        if start > end:
            raise ValueError('Invalid range')
        template = result['history'][-1]
        result['history'] = [{**deepcopy(template), 'date': (start + timedelta(days=offset)).isoformat()}
                             for offset in range((end - start).days + 1)]
        result.update(range_start=start_date, range_end=report_date, report_date=report_date,
                      today=result['history'][-1])
    return result


manager.get_dashboard_trend_data.side_effect = trend_report

def user_report(*args, **kwargs):
    time.sleep(state.get('user_report_delay', 0))
    return {**report(*args, **kwargs), 'completed_period': {'loading': True}}


def user_completion(*args, **kwargs):
    time.sleep(state.get('user_completion_delay', 0))
    return {'start': '2026-10-01', 'end': '2026-10-06', 'available': True,
            'rows': [{'User': user, 'seconds': seconds(), 'tasks': 2} for user in people()]}


manager.get_user_report_data.side_effect = user_report
namespace['get_user_completion_data'] = user_completion
manager._get_reporting_name.side_effect = lambda uid, username: username


def user_batches(_manager, report_date, force_refresh=False):
    return {'report_date': report_date, 'available': state['mode'] != 'failure', 'unresolved': [],
            'batches': [{'batch_id': f'fixture-{user}-{index}', 'username': user,
                         'batch_date': report_date, 'status': 'batch_in_progress', 'task_count': 2,
                         'total_duration_seconds': seconds(), 'report_scope': scope}
                        for user in people() for index, scope in enumerate(('Assigned', 'Assigned', 'Worked'))]}


# Keep the independent batch callback synthetic, like every other preview source.
batch_source_patch = patch('slicing_dashboard.reporting.user_batches.get_user_batches_data', user_batches)
batch_source_patch.start()

# Optional inspection of a retained local capture; all API transports remain fake.
if len(sys.argv) > 1:
    import json
    from slicing_dashboard.reporting.dashboard_reports import date_range, day_for_ui, report_users
    captured = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    end = captured['date']
    history = []
    for day in date_range(end):
        path = Path('data/reports/daily-work') / f'{day}.json'
        saved = captured if day == end else json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        history.append(day_for_ui(saved, day))
    payload = {'report_date': end, 'previous_date': history[-2]['date'],
               'history': history, 'today': history[-1], 'yesterday': history[-2],
               'users': report_users({}, history=history)}
    # Keep the zero-output people from the configured local roster as well.
    mapping = json.loads(Path('config/user_mapping.json').read_text(encoding='utf-8'))
    payload['users'] = report_users(mapping, history=history)
    manager.get_daily_report_data.side_effect = lambda *args, **kwargs: deepcopy(payload)
    manager.get_saved_daily_report_data.side_effect = lambda *args, **kwargs: {**deepcopy(payload), 'saved_only': True, 'refresh_pending': False}
    manager.get_dashboard_trend_data.side_effect = lambda *args, **kwargs: deepcopy(payload)
    from tests.test_daily_report_page import components
    for component in components(app.layout):
        if getattr(component, 'id', None) == 'daily-report-date':
            component.value = end


@app.server.route('/qa/scenario', methods=['POST'])
def scenario():
    state.update(request.get_json() or {})
    review_manager.invalidate_pending_review()
    if state.pop('seed_panels', False):
        delays = {key: state.get(key, 0) for key in ('rework_delay', 'dashboard_delay', 'delay')}
        for key in delays:
            state[key] = 0
        seed_panels()
        state.update(delays)
    return state


@app.server.route('/qa/state')
def status():
    return state


if __name__ == '__main__':
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=8059, debug=False)
