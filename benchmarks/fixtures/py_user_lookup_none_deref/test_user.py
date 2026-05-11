"""Tests for user.py — the missing-id case currently raises AttributeError."""

import pytest

from user import get_display_name, lookup


def test_lookup_existing():
    assert lookup(1)["name"] == "alice"


def test_lookup_missing():
    """A miss should return None — currently passes."""
    assert lookup(999) is None


def test_display_name_existing():
    assert get_display_name(1) == "ALICE"


def test_display_name_missing_returns_unknown():
    """A miss should return a sentinel like 'UNKNOWN', not crash."""
    assert get_display_name(999) == "UNKNOWN"
