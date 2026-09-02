"""
Tests for duration and date parsing.

Covers the various duration formats specified in the project overview:
- MM:SS
- HH:MM:SS
- H:MM:SS
- Decimal minutes
- Text-based ("10 min", "1h 30m")
- Edge cases (empty, invalid, negative)
"""

import pytest

from slicing_dashboard.extraction.parsers import format_duration, parse_duration_string


class TestParseDurationString:
    """Tests for parse_duration_string()."""

    # ── HH:MM:SS format ─────────────────────────────────────────────

    def test_hhmmss_standard(self) -> None:
        assert parse_duration_string("01:30:00") == 5400.0

    def test_hhmmss_with_seconds(self) -> None:
        assert parse_duration_string("01:45:20") == 6320.0

    def test_hhmmss_zero(self) -> None:
        assert parse_duration_string("00:00:00") == 0.0

    def test_hhmmss_large_hours(self) -> None:
        assert parse_duration_string("10:33:00") == 37980.0

    def test_hhmmss_ten_minutes_thirtythree_seconds(self) -> None:
        assert parse_duration_string("00:10:33") == 633.0

    # ── MM:SS format ─────────────────────────────────────────────────

    def test_mmss_standard(self) -> None:
        assert parse_duration_string("10:33") == 633.0

    def test_mmss_short(self) -> None:
        assert parse_duration_string("5:30") == 330.0

    def test_mmss_zero(self) -> None:
        assert parse_duration_string("0:00") == 0.0

    # ── Decimal minutes ──────────────────────────────────────────────

    def test_decimal_integer(self) -> None:
        assert parse_duration_string("10") == 600.0

    def test_decimal_float(self) -> None:
        assert parse_duration_string("10.5") == 630.0

    def test_decimal_zero(self) -> None:
        assert parse_duration_string("0") == 0.0

    # ── Text-based formats ───────────────────────────────────────────

    def test_minutes_text(self) -> None:
        assert parse_duration_string("10 min") == 600.0

    def test_minutes_text_plural(self) -> None:
        assert parse_duration_string("10 minutes") == 600.0

    def test_hours_and_minutes(self) -> None:
        assert parse_duration_string("1h 30m") == 5400.0

    def test_hours_only(self) -> None:
        assert parse_duration_string("2h") == 7200.0

    def test_hours_text(self) -> None:
        assert parse_duration_string("2 hours") == 7200.0

    def test_seconds_text(self) -> None:
        assert parse_duration_string("90s") == 90.0

    def test_seconds_text_full(self) -> None:
        assert parse_duration_string("90 seconds") == 90.0

    # ── Edge cases ───────────────────────────────────────────────────

    def test_empty_string(self) -> None:
        assert parse_duration_string("") == 0.0

    def test_whitespace_only(self) -> None:
        assert parse_duration_string("   ") == 0.0

    def test_none_like(self) -> None:
        assert parse_duration_string("") == 0.0

    def test_invalid_string(self) -> None:
        assert parse_duration_string("not a duration") == 0.0

    def test_leading_trailing_whitespace(self) -> None:
        assert parse_duration_string("  10:33  ") == 633.0


class TestFormatDuration:
    """Tests for format_duration()."""

    def test_zero(self) -> None:
        assert format_duration(0) == "00:00:00"

    def test_one_hour(self) -> None:
        assert format_duration(3600) == "01:00:00"

    def test_complex(self) -> None:
        assert format_duration(6320) == "01:45:20"

    def test_large(self) -> None:
        # 10 hours 33 minutes
        assert format_duration(37980) == "10:33:00"

    def test_negative(self) -> None:
        assert format_duration(-100) == "00:00:00"

    def test_five_hours_eight_minutes(self) -> None:
        assert format_duration(18480) == "05:08:00"

    def test_small_seconds(self) -> None:
        assert format_duration(45) == "00:00:45"

    def test_float_truncation(self) -> None:
        # 1 hour 30 minutes and 0.5 seconds → truncates to 1:30:00
        assert format_duration(5400.5) == "01:30:00"


class TestStatusNormalization:
    """Tests for status classification (Normal vs Error)."""

    def test_normal_completions(self) -> None:
        import pandas as pd
        from slicing_dashboard.processing.normalization import normalize_status

        df = pd.DataFrame({"status": ["slice_completed"]})
        df, warnings = normalize_status(df, "status")
        
        assert df.loc[0, "status_normalized"] == "slice_completed"
        assert df.loc[0, "is_completed"] == True
        assert df.loc[0, "completion_type"] == "normal"
        assert len(warnings) == 0

    def test_error_completions(self) -> None:
        import pandas as pd
        from slicing_dashboard.processing.normalization import normalize_status

        df = pd.DataFrame({"status": ["video_error_confirmed"]})
        df, warnings = normalize_status(df, "status")
        
        assert df.loc[0, "status_normalized"] == "video_error_confirmed"
        assert df.loc[0, "is_completed"] == True
        assert df.loc[0, "completion_type"] == "error"
        assert len(warnings) == 0

    def test_pending_statuses(self) -> None:
        import pandas as pd
        from slicing_dashboard.processing.normalization import normalize_status

        df = pd.DataFrame({"status": ["slicing", "slice_submitted", "slice_assigned", "slice_rework"]})
        df, warnings = normalize_status(df, "status")
        
        assert all(df["is_completed"] == False)
        assert all(df["completion_type"] == "none")
        assert len(warnings) == 0

    def test_unknown_status(self) -> None:
        import pandas as pd
        from slicing_dashboard.processing.normalization import normalize_status

        df = pd.DataFrame({"status": ["not_a_real_status"]})
        df, warnings = normalize_status(df, "status")
        
        assert df.loc[0, "status_normalized"] == "unknown"
        assert df.loc[0, "is_completed"] == False
        assert df.loc[0, "completion_type"] == "none"
        assert len(warnings) == 1
        assert "unknown status values" in warnings[0]

    def test_rework_mapping(self) -> None:
        import pandas as pd
        from slicing_dashboard.processing.normalization import normalize_status

        df = pd.DataFrame({
            "id": ["task_1", "task_2", "task_3"],
            "status": ["slice_rework", "slice_rework", "slice_rework"]
        })
        requests = [
            {
                "task_id": "task_1",
                "request_type": "rework_notice",
                "returned_by_name": "anika-auditor02",
                "created_at": "2026-08-26T15:26:29"
            },
            {
                "task_id": "task_2",
                "request_type": "rework_notice",
                "returned_by_name": "SSHD-Leader",
                "created_at": "2026-08-26T15:26:29"
            }
            # task_3 has no matching request notice
        ]
        
        df, warnings = normalize_status(df, "status", requests=requests)
        
        assert df.loc[0, "rework_by"] == "auditor"
        assert df.loc[1, "rework_by"] == "leader"
        assert df.loc[2, "rework_by"] == "leader" # defaults to leader
        assert len(warnings) == 0
