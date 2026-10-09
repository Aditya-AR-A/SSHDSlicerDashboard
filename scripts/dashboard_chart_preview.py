"""Local-only dashboard QA fixture. Never reads production credentials or storage."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import sys
import time
import logging
from unittest.mock import MagicMock

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
    rows = [dict(user_id=i, username=name, **{
        key: value for stage in ('leader', 'auditor', 'admin') for key, value in
        ((f'{stage}_review_duration_seconds', seconds() if stage == 'leader' else state.get(stage + '_seconds', 0)),
         (f'{stage}_review_count', 1 if stage == 'leader' else 0))}) for i, name in enumerate(people())]
    result = MagicMock()
    result.json.return_value = {'items': rows, 'total': len(rows)}
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
    kpis = {key: 1 for key in ('total_approved_duration', 'approved_pct', 'prev_approved_duration',
        'total_pending_duration', 'leader_review_duration', 'auditor_review_duration', 'admin_review_duration',
        'rework_duration', 'rework_pct', 'prev_rework_duration', 'total_error_duration', 'error_pct',
        'prev_error_duration', 'completed_tasks', 'total_tasks', 'completed_pct', 'prev_completed_tasks')}
    if state['mode'] in ('zero', 'large'):
        kpis = {key: (0 if state['mode'] == 'zero' else 1e18) for key in kpis}
    kpis.update(assignable_duration=None if state['mode'] == 'failure' else 0, assignable_count=0,
                assignable_error='Fixture failure' if state['mode'] == 'failure' else None)
    complete = pd.DataFrame([{'User': user, 'Completed Duration': seconds()} for user in people()])
    ratios = pd.DataFrame([{'User': user, 'Total Batches': 2, 'No Rework': 1, '1 Rework': 1} for user in people()])
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


manager.get_daily_report_data.side_effect = report
manager.get_user_report_data.side_effect = report
manager.get_dashboard_trend_data.side_effect = report

def user_report(*args, **kwargs):
    time.sleep(state.get('user_report_delay', 0))
    return {**report(*args, **kwargs), 'completed_period': {'loading': True}}


def user_completion(*args, **kwargs):
    time.sleep(state.get('user_completion_delay', 0))
    return {'start': '2026-10-01', 'end': '2026-10-06', 'available': True,
            'rows': [{'User': user, 'seconds': seconds(), 'tasks': 2} for user in people()]}


manager.get_user_report_data.side_effect = user_report
namespace['get_user_completion_data'] = user_completion

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
    manager.get_dashboard_trend_data.side_effect = lambda *args, **kwargs: deepcopy(payload)
    from tests.test_daily_report_page import components
    for component in components(app.layout):
        if getattr(component, 'id', None) == 'daily-report-date':
            component.value = end


@app.server.route('/qa/scenario', methods=['POST'])
def scenario():
    state.update(request.get_json() or {})
    review_manager.invalidate_pending_review()
    return state


@app.server.route('/qa/state')
def status():
    return state


if __name__ == '__main__':
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=8059, debug=False)
