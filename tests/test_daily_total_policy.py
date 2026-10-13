"""The current work policy also applies to retained rows and historical views."""
from copy import deepcopy

import pytest

from slicing_dashboard.reporting.dashboard_reports import (
    day_for_ui, prepare_daily_table, prepare_team_composition_rows,
    prepare_user_comparison_rows, prepare_daily_work_chart_rows, summarize_daily,
)
from slicing_dashboard.reporting.approval_reports import prepare_approval_trend
from slicing_dashboard.reporting.user_reports import prepare_user_report
from slicing_dashboard.plots.report_insights import build_activity_calendar, build_day_comparison
from slicing_dashboard.plots.work_breakdown_donut import build_work_breakdown_donut
from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart


@pytest.mark.parametrize('fresh,same,old', [(3600, 1800, 7200), (0, 0, 7200)])
def test_saved_totals_are_recalculated_without_erasing_old_rework(fresh, same, old):
    row = {'User': 'A', 'Total Duration': fresh + same + old, 'Total Tasks': 3,
           'New Videos (First Time)': fresh, 'Same-day Rework': same, 'Old Rework': old,
           'New Tasks': 1, 'Same-day Rework Tasks': 1, 'Old Rework Tasks': 1, 'Reworks': same + old}
    saved = {'date': '2026-10-06', 'rows': [row], 'metadata': {'available': True, 'closed': True}}
    before = deepcopy(saved)
    day = day_for_ui(saved, saved['date'])
    expected = fresh + same
    assert summarize_daily(saved)['total_seconds'] == expected
    assert summarize_daily(day)['old_rework_seconds'] == old
    assert day['rows'][0]['Reworks'] == same
    assert prepare_daily_table(day, numeric_durations=True)[-1]['Total Duration'] == expected / 3600
    assert prepare_team_composition_rows([day])[0]['total_seconds'] == expected
    assert prepare_user_comparison_rows([day], ['A'])[0]['total_seconds'] == expected
    payload = {'report_date': day['date'], 'previous_date': day['date'], 'history': [day],
               'users': ['A'], 'days': {day['date']: day}}
    report = prepare_user_report(payload, 'A')
    assert report['summary']['total_seconds'] == expected
    assert report['overall']['seconds'] == expected
    assert prepare_approval_trend(payload)['rows'][0]['total_seconds'] == expected
    comparison = build_day_comparison(day, day, ['A'])
    assert comparison.data[0].x[0] == expected / 3600
    calendar, count = build_activity_calendar(payload['days'], 'A', day['date'])
    assert count == 1
    assert [value for values in calendar.data[1].z for value in values if value is not None] == [expected / 3600]
    donut = build_work_breakdown_donut(report['summary'])
    if expected:
        assert sum(donut.data[0].values) == expected
        assert report['summary']['rework_percentage'] == same / expected * 100
    else:
        assert not donut.data
        assert report['summary']['rework_percentage'] is None
    assert saved == before


def test_work_chart_matches_comparison_for_duplicate_rows_and_zero_roster():
    # Two account summaries for one person must become one chart entry. Large
    # old rework is separate evidence and must not increase the selected-day bar.
    rows = [{'User': 'A', 'New Videos (First Time)': fresh, 'Same-day Rework': same,
             'Old Rework': old, 'Total Duration': fresh + same + old, 'Total Tasks': 1,
             'RawID': account}
            for fresh, same, old, account in [(3600, 0, 11102, 'account-one'),
                                              (0, 1800, 0, 'account-two')]]
    day = {'date': '2026-10-07', 'metadata': {'available': True}, 'rows': rows}
    before = deepcopy(day)
    roster = ['A', 'Annotator', 'Sanddep', 'Sheetal']
    frame = prepare_daily_work_chart_rows(day, roster)
    assert frame['User'].tolist() == roster
    assert frame.iloc[0]['IDs'] == 'account-one,account-two'
    work = build_error_rework_chart(frame, '07 Oct 2026', True)
    comparison = build_day_comparison(day, day, roster)
    totals = {user: sum(trace.y[list(trace.x).index(user)] for trace in work.data if trace.type == 'bar')
              for user in roster}
    assert totals == dict(zip(comparison.data[0].y, comparison.data[0].x))
    assert totals == {'A': 1.5, 'Annotator': 0, 'Sanddep': 0, 'Sheetal': 0}
    trend = prepare_user_comparison_rows([day], roster)
    assert {row['user']: row['total_seconds'] / 3600 for row in trend} == totals
    assert day == before


def test_work_chart_does_not_invent_zero_rows_for_an_unavailable_day():
    day = {'date': '2026-10-07', 'metadata': {'available': False}, 'rows': []}
    assert prepare_daily_work_chart_rows(day, ['A', 'B']).empty
