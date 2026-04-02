"""
Playlist and playlist metadata helpers for rythmx.db.
"""
from __future__ import annotations

import time
from typing import Callable

import sqlite3


def save_playlist(
    connect: Callable[[], sqlite3.Connection],
    tracks: list[dict],
    playlist_name: str = "For You",
) -> None:
    """Replace the current playlist with a new scored track list."""
    with connect() as conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_name = ?", (playlist_name,))
        for i, t in enumerate(tracks):
            conn.execute(
                """INSERT OR REPLACE INTO playlist_tracks
                   (playlist_name, track_id, spotify_track_id, track_name, artist_name,
                    album_name, album_cover_url, score, position, is_owned, release_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    playlist_name,
                    t.get("plex_rating_key"),
                    t.get("spotify_track_id"),
                    t.get("track_name"),
                    t.get("artist_name"),
                    t.get("album_name"),
                    t.get("album_cover_url"),
                    t.get("score"),
                    i,
                    1 if t.get("is_owned", True) else 0,
                    t.get("release_date"),
                ),
            )


def get_playlist(
    connect: Callable[[], sqlite3.Connection],
    playlist_name: str = "For You",
) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM playlist_tracks WHERE playlist_name = ? ORDER BY position ASC",
            (playlist_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_to_playlist(
    connect: Callable[[], sqlite3.Connection],
    track: dict,
    playlist_name: str = "For You",
) -> None:
    """Append a single track to the playlist (upsert by track_id, ignores duplicates)."""
    with connect() as conn:
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_tracks WHERE playlist_name = ?",
            (playlist_name,),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO playlist_tracks
               (playlist_name, track_id, spotify_track_id, track_name, artist_name,
                album_name, album_cover_url, score, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(track_id) DO NOTHING""",
            (
                playlist_name,
                track.get("track_id"),
                track.get("spotify_track_id"),
                track.get("track_name"),
                track.get("artist_name"),
                track.get("album_name"),
                track.get("album_cover_url"),
                track.get("score"),
                next_pos,
            ),
        )


def remove_from_playlist(
    connect: Callable[[], sqlite3.Connection],
    track_id: str,
    playlist_name: str = "For You",
) -> None:
    """Remove a track from the playlist by track_id."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM playlist_tracks WHERE track_id = ? AND playlist_name = ?",
            (track_id, playlist_name),
        )


def update_playlist_plex_id(
    connect: Callable[[], sqlite3.Connection],
    playlist_name: str,
    plex_playlist_id: str,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE playlist_tracks SET plex_playlist_id = ? WHERE playlist_name = ?",
            (plex_playlist_id, playlist_name),
        )


def create_playlist_meta(
    connect: Callable[[], sqlite3.Connection],
    name: str,
    source: str = "manual",
    source_url: str | None = None,
    auto_sync: bool = False,
    mode: str = "library_only",
    max_tracks: int = 50,
) -> None:
    """Create playlist metadata; updates source/mode for Forge new-music sourced rows."""
    with connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO playlists (name, source, source_url, auto_sync, mode, max_tracks)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, source, source_url, 1 if auto_sync else 0, mode, max_tracks),
        )
        if source == "new_music":
            conn.execute(
                "UPDATE playlists SET source=?, mode=? WHERE name=?",
                (source, mode, name),
            )


def get_playlist_meta(
    connect: Callable[[], sqlite3.Connection],
    name: str,
) -> dict | None:
    """Return metadata for a single playlist, or None if not found."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM playlists WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def update_playlist_meta(
    connect: Callable[[], sqlite3.Connection],
    name: str,
    auto_sync: bool | None = None,
    mode: str | None = None,
    source_url: str | None = None,
    max_tracks: int | None = None,
) -> None:
    """Update mutable fields on an existing playlist metadata row."""
    with connect() as conn:
        if auto_sync is not None:
            conn.execute(
                "UPDATE playlists SET auto_sync = ? WHERE name = ?",
                (1 if auto_sync else 0, name),
            )
        if mode is not None:
            conn.execute(
                "UPDATE playlists SET mode = ? WHERE name = ?",
                (mode, name),
            )
        if source_url is not None:
            conn.execute(
                "UPDATE playlists SET source_url = ? WHERE name = ?",
                (source_url, name),
            )
        if max_tracks is not None:
            conn.execute(
                "UPDATE playlists SET max_tracks = ? WHERE name = ?",
                (int(max_tracks), name),
            )


def mark_playlist_synced(connect: Callable[[], sqlite3.Connection], name: str) -> None:
    """Update last_synced_ts to now for a playlist."""
    with connect() as conn:
        conn.execute(
            "UPDATE playlists SET last_synced_ts = ? WHERE name = ?",
            (int(time.time()), name),
        )


def list_playlists(connect: Callable[[], sqlite3.Connection]) -> list[dict]:
    """
    Return all playlists with track/owned counts.
    Includes playlists with tracks but no metadata row.
    """
    with connect() as conn:
        agg_rows = conn.execute(
            """
            SELECT playlist_name,
                   COUNT(*) AS track_count,
                   SUM(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END) AS owned_count
            FROM playlist_tracks
            GROUP BY playlist_name
            """
        ).fetchall()
        agg = {r["playlist_name"]: dict(r) for r in agg_rows}

        meta_rows = conn.execute(
            "SELECT * FROM playlists ORDER BY created_at DESC"
        ).fetchall()
        meta = {r["name"]: dict(r) for r in meta_rows}

        all_names = set(agg.keys()) | set(meta.keys())
        result = []
        for name in all_names:
            m = meta.get(name, {})
            a = agg.get(name, {"track_count": 0, "owned_count": 0})
            result.append(
                {
                    "name": name,
                    "source": m.get("source", "manual"),
                    "source_url": m.get("source_url"),
                    "auto_sync": bool(m.get("auto_sync", 0)),
                    "mode": m.get("mode", "library_only"),
                    "max_tracks": m.get("max_tracks", 50),
                    "last_synced_ts": m.get("last_synced_ts", 0),
                    "created_at": m.get("created_at"),
                    "track_count": a["track_count"],
                    "owned_count": a["owned_count"],
                }
            )
        result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return result


def delete_playlist(connect: Callable[[], sqlite3.Connection], name: str) -> None:
    """Delete a playlist and all its tracks."""
    with connect() as conn:
        conn.execute("DELETE FROM playlists WHERE name = ?", (name,))
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_name = ?", (name,))


def rename_playlist(
    connect: Callable[[], sqlite3.Connection],
    old_name: str,
    new_name: str,
) -> None:
    """Rename a playlist in metadata and playlist tracks."""
    with connect() as conn:
        conn.execute("UPDATE playlists SET name = ? WHERE name = ?", (new_name, old_name))
        conn.execute(
            "UPDATE playlist_tracks SET playlist_name = ? WHERE playlist_name = ?",
            (new_name, old_name),
        )


def remove_playlist_row(connect: Callable[[], sqlite3.Connection], row_id: int) -> None:
    """Remove a single track row from playlist_tracks by primary key id."""
    with connect() as conn:
        conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (row_id,))
