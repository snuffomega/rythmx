"""
Forge published playlist persistence helpers for rythmx.db.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

import sqlite3


def _dedupe_track_ids(track_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in track_ids:
        tid = str(raw or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    return ordered


def upsert_forge_playlist(
    connect: Callable[[], sqlite3.Connection],
    playlist_id: str,
    name: str,
    track_ids: list[str],
    pushed_at: str | None = None,
) -> dict:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    safe_name = (name or "").strip() or "Untitled Build"
    safe_track_ids = _dedupe_track_ids(track_ids)
    safe_pushed_at = pushed_at or now

    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, created_at
            FROM forge_playlists
            WHERE id = ?
            """,
            (playlist_id,),
        ).fetchone()
        created_at = row["created_at"] if row else now

        conn.execute(
            """
            INSERT OR REPLACE INTO forge_playlists
                (id, name, created_at, updated_at, plex_push_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (playlist_id, safe_name, created_at, now, safe_pushed_at),
        )
        conn.execute(
            "DELETE FROM forge_playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        for idx, track_id in enumerate(safe_track_ids):
            conn.execute(
                """
                INSERT INTO forge_playlist_tracks (playlist_id, track_id, position, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (playlist_id, track_id, idx, now),
            )

    return {
        "id": playlist_id,
        "name": safe_name,
        "track_count": len(safe_track_ids),
        "updated_at": now,
        "pushed_at": safe_pushed_at,
    }
