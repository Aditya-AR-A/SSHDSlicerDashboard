import pytest

from slicing_dashboard.plots.theme import format_seconds, duration_ticks
from slicing_dashboard.plots.tables import sort_duration_rows


@pytest.mark.parametrize('seconds, expected', [
    (0, '00:00:00'), (59, '00:00:59'), (3599.5, '01:00:00'),
    (3661, '01:01:01'), (25 * 3600 + 61, '25:01:01'), (100 * 3600, '100:00:00'),
    (None, 'Unavailable'), (float('inf'), 'Unavailable'),
])
def test_elapsed_time_handles_seconds_carry_and_hours_beyond_a_day(seconds, expected):
    assert format_seconds(seconds) == expected


def test_duration_ticks_keep_numeric_hours_and_clock_labels():
    values, labels = duration_ticks(0, 3.3)
    assert values == [0, 1, 2, 3]
    assert labels == ['00:00', '01:00', '02:00', '03:00']
    values, labels = duration_ticks(0, 10 / 3600)
    assert labels == ['00:00:00', '00:00:02', '00:00:04', '00:00:06', '00:00:08', '00:00:10']
    assert values[-1] == pytest.approx(10 / 3600)


@pytest.mark.parametrize('direction, expected', [
    ('asc', ['02:00:00', '25:00:00', '99:59:59', '100:00:00', None]),
    ('desc', ['100:00:00', '99:59:59', '25:00:00', '02:00:00', None]),
])
def test_table_sort_uses_elapsed_time_and_keeps_missing_values_last(direction, expected):
    rows = [{'Duration': value} for value in ['100:00:00', '02:00:00', None, '99:59:59', '25:00:00']]
    sorted_rows = sort_duration_rows(rows, [{'column_id': 'Duration', 'direction': direction}], ['Duration'])
    assert [row['Duration'] for row in sorted_rows] == expected
    assert rows[0]['Duration'] == '100:00:00'
