"""
Image cache and related image lookup helpers for rythmx.db.
"""
from __future__ import annotations

from typing import Callable

import sqlite3


def get_image_cache(connect: Callable[[], sqlite3.Connection], entity_type: str, entity_key: str) -> str | None:
    """Return cached image URL or None if not cached / empty."""
    with connect() as conn:
        row = conn.execute(
            "SELECT image_url FROM image_cache WHERE entity_type=? AND entity_key=?",
            (entity_type, entity_key)
        ).fetchone()
        if row is None or not row["image_url"]:
            return None
        conn.execute(
            "UPDATE image_cache SET last_accessed=datetime('now') WHERE entity_type=? AND entity_key=?",
            (entity_type, entity_key)
        )
        return row["image_url"]


def set_image_cache(connect: Callable[[], sqlite3.Connection], entity_type: str, entity_key: str, image_url: str):
    """Upsert an image URL into the cache."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO image_cache (entity_type, entity_key, image_url, last_accessed)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(entity_type, entity_key) DO UPDATE SET
                   image_url=excluded.image_url,
                   last_accessed=datetime('now')""",
            (entity_type, entity_key, image_url)
        )


def clear_image_cache(connect: Callable[[], sqlite3.Connection]):
    """Delete all rows from image_cache."""
    with connect() as conn:
        conn.execute("DELETE FROM image_cache")


def get_release_itunes_album_id(connect: Callable[[], sqlite3.Connection], artist_name: str, album_title: str) -> str | None:
    """Return itunes_album_id for a known artist+album, or None."""
    with connect() as conn:
        row = conn.execute(
            """SELECT itunes_album_id FROM lib_releases
               WHERE artist_name_lower = lower(?)
                 AND title_lower = lower(?)
                 AND itunes_album_id IS NOT NULL
               LIMIT 1""",
            (artist_name, album_title),
        ).fetchone()
    return row["itunes_album_id"] if row else None


def get_missing_image_entities(connect: Callable[[], sqlite3.Connection], limit: int = 40) -> list[tuple[str, str, str]]:
    """
    Return up to `limit` (entity_type, name, artist) tuples for entities that
    have no resolved image in image_cache.
    """
    with connect() as conn:
        albums = conn.execute("""
            SELECT DISTINCT 'album', album_name, artist_name
            FROM playlist_tracks
            WHERE album_name IS NOT NULL AND album_name != ''
              AND NOT EXISTS (
                  SELECT 1 FROM image_cache
                  WHERE entity_type = 'album'
                    AND entity_key = lower(playlist_tracks.artist_name) || '|||' || lower(playlist_tracks.album_name)
                    AND image_url != ''
              )
            LIMIT ?
        """, (limit,)).fetchall()

        remaining = limit - len(albums)
        artists = []
        if remaining > 0:
            artists = conn.execute("""
                SELECT DISTINCT 'artist', lastfm_name, ''
                FROM artist_identity_cache
                WHERE lastfm_name IS NOT NULL AND lastfm_name != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM image_cache
                      WHERE entity_type = 'artist'
                        AND entity_key = lower(artist_identity_cache.lastfm_name)
                        AND image_url != ''
                  )
                LIMIT ?
            """, (remaining,)).fetchall()

        return [(r[0], r[1], r[2]) for r in albums + artists]

