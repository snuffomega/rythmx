from __future__ import annotations

import pytest

from app.db.sql_helpers import build_in_clause


def test_build_in_clause_builds_expected_placeholders():
    assert build_in_clause(1) == "(?)"
    assert build_in_clause(3) == "(?,?,?)"


def test_build_in_clause_rejects_non_positive_counts():
    with pytest.raises(ValueError):
        build_in_clause(0)
