"""
Application settings helpers for rythmx.db.
"""
from __future__ import annotations

from typing import Callable

import sqlite3


def get_setting(connect: Callable[[], sqlite3.Connection], key: str, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(connect: Callable[[], sqlite3.Connection], key: str, value: str):
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def get_all_settings(connect: Callable[[], sqlite3.Connection]) -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

