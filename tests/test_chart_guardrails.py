import math
from unittest.mock import MagicMock

import pandas as pd
import pytest

from slicing_dashboard.plots.individual_chart import build_individual_chart
from slicing_dashboard.plots.pending_chart import build_pending_chart
from slicing_dashboard.plots.assigned_chart import build_assigned_chart
from slicing_dashboard.plots.rework_ratio_chart import build_rework_ratio_chart
from slicing_dashboard.plots.work_trend_chart import build_user_comparison_chart
from slicing_dashboard.plots.report_insights import build_activity_calendar
from slicing_dashboard.plots.theme import format_seconds
from slicing_dashboard.processing.batch_ratios import batch_ratios


@pytest.mark.parametrize('frame', [None, pd.DataFrame(), pd.DataFrame({'User': [None]}),
                                  pd.DataFrame({'User': ['A'], 'Duration': [float('inf')], 'Count': [1], 'Stage': ['Pending Leader']})])
def test_malformed_chart_is_isolated_and_empty_axes_are_hidden(frame):
    for builder in (lambda: build_pending_chart(frame, True),
                    lambda: build_individual_chart(frame, '', '', True),
                    lambda: build_assigned_chart(frame, True),
                    lambda: build_rework_ratio_chart(frame, '', True)):
        figure = builder()
        assert not figure.data and figure.layout.annotations
        assert figure.layout.xaxis.visible is False
        figure.to_json()


@pytest.mark.parametrize('seconds', [0, .000001, 1e24])
def test_scales_keep_finite_extreme_values_and_full_names(seconds):
    names = ['Very long full username ' + str(i) * 35 for i in range(50)]
    frame = pd.DataFrame({'User': names, 'Completed Duration': [seconds] * 50})
    figure = build_individual_chart(frame, '2026-10-01', '2026-10-07', True)
    assert sum(len(trace.x) for trace in figure.data) == 50
    assert sum(sum(trace.y) for trace in figure.data) == pytest.approx(seconds / 3600 * 50)
    assert all(math.isfinite(value) for value in figure.layout.yaxis.range)
    assert all(len(label) <= 16 for label in figure.layout.xaxis.ticktext)
    assert set(value for trace in figure.data for value in trace.x) == set(names)


def test_missing_durations_are_not_formatted_as_zero():
    assert format_seconds(0) == '00:00'
    for value in (None, float('nan'), float('inf'), 'invalid'):
        assert format_seconds(value) == 'Unavailable'
    assert format_seconds(.001) == '0.001s'


def test_duplicate_daily_records_cannot_overwrite_another_value():
    rows = [{'date': '2026-10-01', 'user': 'A', 'total_seconds': value} for value in [1, 9]]
    figure = build_user_comparison_chart(rows)
    assert not figure.data and 'unavailable' in figure.layout.annotations[0].text
    calendar, count = build_activity_calendar({}, 'A', 'invalid')
    assert count == 0 and not calendar.data


def test_batch_distribution_counts_entities_and_unique_returns_only():
    batches = [dict(batch_id='one', batch_date='2026-10-01', status='batch_completed', total_duration_seconds=720000),
               dict(batch_id='two', batch_date='2026-10-01', status='batch_member_assigned', total_duration_seconds=100)]
    event = dict(event_id='return-one', batch_id='one', returned_at='2026-10-01T03:00:00Z')
    frame = batch_ratios(batches, [event, event], '2026-10-01', '2026-10-07', lambda batch: 'A')
    assert frame.iloc[0]['Total Batches'] == 1
    assert frame.iloc[0]['1 Rework'] == 1
    assert frame.iloc[0]['Total Duration (hrs)'] == 200


def test_ratio_does_not_generate_batches_from_efficiency_or_current_rework():
    from slicing_dashboard.data_manager import DataManager
    dm = DataManager.__new__(DataManager)
    dm._batches_master_cache = {}
    dm.sync_batches_master = MagicMock()
    dm.sync_batch_returns = MagicMock(return_value=[])
    dm.fetch_annotator_efficiency = MagicMock(return_value=({}, [{'completed_duration_seconds': 500000}]))
    dm.get_live_rework_by_user = MagicMock(return_value={'A': {'count': 500}})
    assert dm.get_batch_rework_ratio_df('2026-10-01', '2026-10-07').empty
    dm.fetch_annotator_efficiency.assert_not_called()
    dm.get_live_rework_by_user.assert_not_called()


@pytest.mark.parametrize('dark', [True, False])
def test_visible_stack_totals_are_outside_and_axis_has_ten_percent_headroom(dark):
    frame = pd.DataFrame({'User': ['A', 'A', 'B'], 'Stage': ['Pending Leader', 'Pending Admin', 'Pending Leader'],
                          'Duration': [3600, 7200, 1800], 'Count': [1, 2, 1]})
    pending = build_pending_chart(frame, dark)
    labels = {item.x: item for item in pending.layout.annotations if item.name == 'bar-hour-total'}
    assert labels['A'].text == '3.00h' and labels['A'].y == 3
    assert labels['B'].text == '0.50h'
    assert all(item.yanchor == 'bottom' and item.yshift > 0 for item in labels.values())
    assert list(pending.layout.yaxis.range) == pytest.approx([0, 3.3])
    assigned = build_assigned_chart(frame.assign(Stage=['New Assigned', 'Rework Assigned', 'New Assigned']), dark)
    labels = {item.y: item for item in assigned.layout.annotations if item.name == 'bar-hour-total'}
    assert labels['A'].text == '3.00h' and labels['A'].x == 3
    assert all(item.xanchor == 'left' and item.xshift > 0 for item in labels.values())
    assert list(assigned.layout.xaxis.range) == pytest.approx([0, 3.3])
    assert sum(sum(trace.x) for trace in assigned.data if trace.type == 'bar') == 3.5


def test_daily_bar_total_excludes_old_rework_but_keeps_its_marker_in_range():
    from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart
    frame = pd.DataFrame({'User': ['A'], 'New Work Duration': [3600], 'Same-day Rework Duration': [1800],
                          'Old Rework Duration': [18000], 'Total Work Duration': [5400]})
    figure = build_error_rework_chart(frame, 'Yesterday', True)
    label = next(item for item in figure.layout.annotations if item.name == 'bar-hour-total')
    assert (label.text, label.y) == ('1.50h', 1.5)
    assert list(figure.layout.yaxis.range) == pytest.approx([0, 5.5])
    assert len(figure.data) == 3  # Labels do not add extra series or duplicate table rows.


def test_composition_totals_keep_unknown_gaps_and_verified_zero():
    from slicing_dashboard.plots.work_trend_chart import build_work_composition_chart
    rows = [{'date': '2026-10-07', 'new_seconds': None, 'same_day_rework_seconds': None,
             'old_rework_seconds': None, 'total_seconds': None},
            {'date': '2026-10-08', 'new_seconds': 0, 'same_day_rework_seconds': 0,
             'old_rework_seconds': 0, 'total_seconds': 0}]
    figure = build_work_composition_chart(rows)
    labels = [item for item in figure.layout.annotations if item.name == 'bar-hour-total']
    assert len(labels) == 1 and labels[0].x == '2026-10-08' and labels[0].text == '0.00h'
    assert list(figure.layout.yaxis.range) == [0, 1]


@pytest.mark.parametrize('seconds', [.000001, 1e24])
def test_extreme_hour_labels_remain_compact_without_rounding_tiny_work_to_zero(seconds):
    frame = pd.DataFrame({'User': ['A'], 'Stage': ['New Assigned'], 'Duration': [seconds]})
    figure = build_assigned_chart(frame, True)
    label = next(item for item in figure.layout.annotations if item.name == 'bar-hour-total')
    assert len(label.text) <= 12 and label.text != '0.00h'
    assert figure.layout.xaxis.range[1] == pytest.approx(seconds / 3600 * 1.1)


def test_completed_bar_labels_remain_outside_after_shared_guardrails():
    figure = build_individual_chart(pd.DataFrame({'User': ['A'], 'Completed Duration': [9000]}), '', '', True)
    assert figure.data[0].text[0] == '2.50h'
    assert figure.data[0].textposition == 'outside' and figure.data[0].cliponaxis is False
    assert list(figure.layout.yaxis.range) == pytest.approx([0, 2.75])
