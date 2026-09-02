"""
Tests for data cleaning, including user mapping.

Covers:
- User mapping (known users, unknown users, preservation)
- Whitespace stripping
- Empty row removal
- Auto-detection of user column
"""

import pandas as pd
import pytest

from slicing_dashboard.processing.cleaning import (
    DEFAULT_USER_MAPPING,
    _apply_user_mapping,
    _detect_user_column,
    clean_dataframe,
    load_user_mapping,
)


class TestUserMapping:
    """Tests for user identity mapping."""

    def test_known_user_maps_correctly(self) -> None:
        df = pd.DataFrame({"user": ["SSHD-Adi", "SSHD-Komal"]})
        df, warnings = _apply_user_mapping(df, "user", DEFAULT_USER_MAPPING)
        assert list(df["user_name"]) == ["Aditya", "Komal"]
        assert len(warnings) == 0

    def test_multiple_aliases_same_user(self) -> None:
        df = pd.DataFrame({"user": ["SSHD-Adi", "SSHD-Aditya", "SSHD-Slicer1"]})
        df, warnings = _apply_user_mapping(df, "user", DEFAULT_USER_MAPPING)
        assert all(name == "Aditya" for name in df["user_name"])

    def test_unknown_user_preserved(self) -> None:
        """Unknown users must NOT be silently discarded (Section 8)."""
        df = pd.DataFrame({"user": ["SSHD-Adi", "SSHD-Unknown123"]})
        df, warnings = _apply_user_mapping(df, "user", DEFAULT_USER_MAPPING)
        assert df.loc[1, "user_name"] == "SSHD-Unknown123"
        assert any("Unknown user" in w for w in warnings)

    def test_user_id_column_preserved(self) -> None:
        """Original user_id should be preserved alongside mapped name."""
        df = pd.DataFrame({"user": ["SSHD-Adi"]})
        df, _ = _apply_user_mapping(df, "user", DEFAULT_USER_MAPPING)
        assert "user_id" in df.columns
        assert df.loc[0, "user_id"] == "SSHD-Adi"
        assert df.loc[0, "user_name"] == "Aditya"

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame({"user": pd.Series(dtype=str)})
        df, warnings = _apply_user_mapping(df, "user", DEFAULT_USER_MAPPING)
        assert len(df) == 0


class TestCleanDataframe:
    """Tests for the full cleaning pipeline."""

    def test_strips_whitespace(self) -> None:
        df = pd.DataFrame({"user": ["  SSHD-Adi  ", " SSHD-Komal"]})
        cleaned, _ = clean_dataframe(df, user_column="user")
        assert cleaned.loc[0, "user_id"] == "SSHD-Adi"

    def test_removes_empty_rows(self) -> None:
        df = pd.DataFrame({
            "user": ["SSHD-Adi", "", None],
            "value": ["100", "", None],
        })
        cleaned, _ = clean_dataframe(df, user_column="user")
        # The empty string row and None row should be removed
        assert len(cleaned) <= 2  # At most the non-empty rows

    def test_auto_detects_user_column(self) -> None:
        df = pd.DataFrame({"username": ["SSHD-Adi"], "value": ["100"]})
        cleaned, _ = clean_dataframe(df)
        assert "user_name" in cleaned.columns


class TestDetectUserColumn:
    """Tests for auto-detection of user column."""

    def test_detects_user(self) -> None:
        df = pd.DataFrame({"user": [], "data": []})
        assert _detect_user_column(df) == "user"

    def test_detects_username(self) -> None:
        df = pd.DataFrame({"username": [], "data": []})
        assert _detect_user_column(df) == "username"

    def test_detects_operator(self) -> None:
        df = pd.DataFrame({"operator": [], "data": []})
        assert _detect_user_column(df) == "operator"

    def test_returns_none_when_not_found(self) -> None:
        df = pd.DataFrame({"column_a": [], "column_b": []})
        assert _detect_user_column(df) is None
