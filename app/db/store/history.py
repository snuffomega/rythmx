"""
History table helpers for rythmx.db.
"""
from __future__ import annotations

from typing import Callable

import sqlite3


def add_history_entry(
    connect: Callable[[], sqlite3.Connection],
    track: dict,
    status: str,
    reason: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO history
               (track_name, artist_name, album_name, source, score, acquisition_status, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                track.get("track_name"),
                track.get("artist_name"),
                track.get("album_name"),
                track.get("source"),
                track.get("score"),
                status,
                reason,
            ),
        )


def get_history(connect: Callable[[], sqlite3.Connection], limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY cycle_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def is_release_in_history(
    connect: Callable[[], sqlite3.Connection],
    artist_name: str,
    album_name: str,
) -> bool:
    """
    Return True if this artist+album was already identified or queued in a previous cycle.
    Used to prevent re-adding the same unowned release every run.
    """
    with connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM history
               WHERE lower(artist_name) = lower(?)
               AND lower(album_name) = lower(?)
               AND acquisition_status IN ('identified', 'queued', 'success')
               LIMIT 1""",
            (artist_name, album_name),
        ).fetchone()
        return row is not None


def get_history_summary(connect: Callable[[], sqlite3.Connection]) -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN acquisition_status = 'queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN acquisition_status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN acquisition_status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN acquisition_status = 'skipped' THEN 1 ELSE 0 END) as skipped
            FROM history
            """
        ).fetchone()
        return dict(row) if row else {}


def clear_history(connect: Callable[[], sqlite3.Connection]) -> None:
    """Delete all rows from history."""
    with connect() as conn:
        conn.execute("DELETE FROM history")
