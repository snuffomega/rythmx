"""
Download queue helpers for rythmx.db.
"""
from __future__ import annotations

from typing import Callable

import sqlite3


def is_in_queue(
    connect: Callable[[], sqlite3.Connection],
    artist_name: str,
    album_title: str,
) -> bool:
    """
    Return True if this release has a pending or submitted acquisition request.
    Does NOT block 'found', 'failed', or 'skipped' - those can be re-evaluated.
    """
    with connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM download_queue
               WHERE lower(artist_name) = lower(?)
               AND lower(album_title) = lower(?)
               AND status IN ('pending', 'submitted')
               LIMIT 1""",
            (artist_name, album_title),
        ).fetchone()
        return row is not None


def add_to_queue(
    connect: Callable[[], sqlite3.Connection],
    artist_name: str,
    album_title: str,
    release_date: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    itunes_album_id: str | None = None,
    deezer_album_id: str | None = None,
    spotify_album_id: str | None = None,
    requested_by: str = "cc",
    playlist_name: str | None = None,
) -> int:
    """
    Insert a release into the download queue. UNIQUE(artist_name, album_title) -
    if an entry already exists (any status), the existing row is left unchanged and
    its id is returned.
    """
    with connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO download_queue
               (artist_name, album_title, release_date, kind, source,
                itunes_album_id, deezer_album_id, spotify_album_id,
                requested_by, playlist_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artist_name,
                album_title,
                release_date,
                kind,
                source,
                itunes_album_id or None,
                deezer_album_id or None,
                spotify_album_id or None,
                requested_by,
                playlist_name,
            ),
        )
        row = conn.execute(
            """SELECT id FROM download_queue
               WHERE lower(artist_name)=lower(?) AND lower(album_title)=lower(?)""",
            (artist_name, album_title),
        ).fetchone()
        return row["id"] if row else -1


def get_queue(
    connect: Callable[[], sqlite3.Connection],
    status: str | None = None,
    playlist_name: str | None = None,
) -> list[dict]:
    """Return queue rows, optionally filtered by status and/or playlist_name."""
    with connect() as conn:
        query = "SELECT * FROM download_queue WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if playlist_name:
            query += " AND playlist_name = ?"
            params.append(playlist_name)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_queue_status(
    connect: Callable[[], sqlite3.Connection],
    queue_id: int,
    status: str,
    provider_response: str | None = None,
) -> None:
    """Update status and updated_at for a queue row."""
    with connect() as conn:
        conn.execute(
            """UPDATE download_queue
               SET status = ?, provider_response = COALESCE(?, provider_response),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, provider_response, queue_id),
        )


def get_queue_stats(connect: Callable[[], sqlite3.Connection]) -> dict:
    """Return counts by status."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM download_queue GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        return {
            "pending": stats.get("pending", 0),
            "submitted": stats.get("submitted", 0),
            "found": stats.get("found", 0),
            "failed": stats.get("failed", 0),
            "skipped": stats.get("skipped", 0),
            "total": sum(stats.values()),
        }


def get_queue_status(
    connect: Callable[[], sqlite3.Connection],
    artist_name: str,
    album_title: str,
) -> str | None:
    """Return the most recent queue status for artist+album, or None if never queued."""
    with connect() as conn:
        row = conn.execute(
            """SELECT status FROM download_queue
               WHERE lower(artist_name) = lower(?) AND lower(album_title) = lower(?)
               ORDER BY created_at DESC LIMIT 1""",
            (artist_name, album_title),
        ).fetchone()
        return row["status"] if row else None
