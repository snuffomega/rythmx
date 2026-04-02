"""
Artist identity cache and artist lookup helpers for rythmx.db.
"""
from __future__ import annotations

import time
from typing import Callable

import sqlite3


def get_lib_artist_ids(connect: Callable[[], sqlite3.Connection], artist_name: str) -> dict | None:
    """
    DB-first lookup of provider IDs from lib_artists for an artist name.
    Returns IDs + match_confidence, or None if no active row exists.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT itunes_artist_id, deezer_artist_id, spotify_artist_id,
                       lastfm_mbid, match_confidence
                FROM lib_artists
                WHERE name_lower = lower(?)
                  AND removed_at IS NULL
                LIMIT 1
                """,
                (artist_name,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def get_cached_artist(connect: Callable[[], sqlite3.Connection], lastfm_name: str) -> dict | None:
    """Return cached provider IDs for a Last.fm artist name, or None if not cached."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM artist_identity_cache WHERE lastfm_name = ?",
            (lastfm_name,),
        ).fetchone()
        return dict(row) if row else None


def cache_artist(
    connect: Callable[[], sqlite3.Connection],
    lastfm_name: str,
    deezer_artist_id: str | None = None,
    spotify_artist_id: str | None = None,
    itunes_artist_id: str | None = None,
    mb_artist_id: str | None = None,
    soulsync_artist_id: str | None = None,
    confidence: int = 80,
    resolution_method: str | None = None,
) -> None:
    """Upsert provider IDs for a Last.fm artist name."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO artist_identity_cache
               (lastfm_name, deezer_artist_id, spotify_artist_id, itunes_artist_id,
                mb_artist_id, soulsync_artist_id, confidence, resolution_method, last_resolved_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(lastfm_name) DO UPDATE SET
                   deezer_artist_id = COALESCE(excluded.deezer_artist_id, deezer_artist_id),
                   spotify_artist_id = COALESCE(excluded.spotify_artist_id, spotify_artist_id),
                   itunes_artist_id = COALESCE(excluded.itunes_artist_id, itunes_artist_id),
                   mb_artist_id = COALESCE(excluded.mb_artist_id, mb_artist_id),
                   soulsync_artist_id = COALESCE(excluded.soulsync_artist_id, soulsync_artist_id),
                   confidence = excluded.confidence,
                   resolution_method = COALESCE(excluded.resolution_method, resolution_method),
                   last_resolved_ts = excluded.last_resolved_ts""",
            (
                lastfm_name,
                deezer_artist_id,
                spotify_artist_id,
                itunes_artist_id,
                mb_artist_id,
                soulsync_artist_id,
                confidence,
                resolution_method,
                int(time.time()),
            ),
        )


def get_artist_navidrome_cover(connect: Callable[[], sqlite3.Connection], artist_name: str) -> str | None:
    """Return the Navidrome coverArt ID for an artist (used by image_service)."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT thumb_url_navidrome FROM lib_artists "
                "WHERE name_lower = lower(?) LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None
