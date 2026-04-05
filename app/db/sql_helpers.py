"""
Shared SQL helpers.
"""
from __future__ import annotations


def build_in_clause(count: int) -> str:
    """
    Return a SQL IN placeholder clause like "(?,?,?)" for the given count.

    Raises ValueError when count < 1 so callers cannot build invalid SQL.
    """
    if count < 1:
        raise ValueError("build_in_clause() requires count >= 1")
    return "(" + ",".join("?" for _ in range(count)) + ")"
