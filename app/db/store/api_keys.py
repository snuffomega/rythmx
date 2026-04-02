"""
API key storage helpers for rythmx.db.
"""
from __future__ import annotations

import secrets
from typing import Callable

import sqlite3


def get_api_key(connect: Callable[[], sqlite3.Connection]) -> str | None:
    """Return the active API key, or None if not yet generated."""
    with connect() as conn:
        row = conn.execute(
            "SELECT key FROM api_keys ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["key"] if row else None


def set_api_key(connect: Callable[[], sqlite3.Connection], key: str) -> None:
    """Replace the active API key."""
    with connect() as conn:
        conn.execute("DELETE FROM api_keys")
        conn.execute("INSERT INTO api_keys (key) VALUES (?)", (key,))


def generate_new_api_key(connect: Callable[[], sqlite3.Connection]) -> str:
    """Generate a cryptographically random 64-char hex API key and persist it."""
    key = secrets.token_hex(32)
    set_api_key(connect, key)
    return key

