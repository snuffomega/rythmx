"""
Taste cache helpers for rythmx.db.
"""
from __future__ import annotations

from typing import Callable

import sqlite3


def upsert_taste_cache(
    connect: Callable[[], sqlite3.Connection],
    artist_name: str,
    play_count: int,
    period: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO taste_cache (artist_name, play_count, period)
               VALUES (?, ?, ?)
               ON CONFLICT(artist_name) DO UPDATE SET
                   play_count = excluded.play_count,
                   period = excluded.period,
                   last_updated = CURRENT_TIMESTAMP""",
            (artist_name, play_count, period),
        )


def get_taste_cache(connect: Callable[[], sqlite3.Connection]) -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT artist_name, play_count FROM taste_cache").fetchall()
        return {r["artist_name"]: r["play_count"] for r in rows}
