"""
library_playlists_service.py — Sync and mutate platform playlists.

Reads from the active LIBRARY_PLATFORM and writes to lib_playlists +
lib_playlist_tracks in rythmx.db.

Rules:
- lib_playlists: INSERT OR REPLACE on sync (full overwrite is safe — this
  table is a derived cache, not enrichment data).
- lib_playlist_tracks: DELETE + re-insert per playlist on each sync.
- lib_tracks rows are matched by platform-native ID (same string stored as
  lib_tracks.id for both Navidrome and Plex).
- forge_playlists / forge_playlist_tracks are never touched.
"""
import logging
import sqlite3

from app import config

logger = logging.getLogger(__name__)


def _connect():
    """Return a WAL-mode connection to rythmx.db."""
    conn = sqlite3.connect(config.RYTHMX_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sync_playlists() -> dict:
    """Sync platform playlists into lib_playlists + lib_playlist_tracks.

    Platform is determined by config.LIBRARY_PLATFORM (navidrome / plex).
    Returns {"playlists_synced": N, "tracks_synced": N}.
    """
    platform = (config.LIBRARY_PLATFORM or "").lower()
    if platform == "navidrome":
        return _sync_navidrome()
    if platform == "plex":
        return _sync_plex()
    raise ValueError(
        f"sync_playlists: unsupported LIBRARY_PLATFORM '{platform}'. "
        "Expected 'navidrome' or 'plex'."
    )


# ---------------------------------------------------------------------------
# Navidrome
# ---------------------------------------------------------------------------

def _sync_navidrome() -> dict:
    from app.db.navidrome_reader import _get_client

    client = _get_client()
    playlists = client.get_playlists()

    playlists_synced = 0
    tracks_synced = 0

    with _connect() as conn:
        for pl in playlists:
            pl_id = pl.get("id")
            if not pl_id:
                continue

            pl_name = pl.get("name", "")
            # coverArt field holds an opaque Navidrome art ID — not a URL;
            # store None here. Image resolution happens via the image service.
            cover_url = None
            track_count = int(pl.get("songCount") or 0)
            # Subsonic duration is in seconds; convert to ms for consistency
            duration_s = pl.get("duration") or 0
            duration_ms = int(duration_s) * 1000
            changed = pl.get("changed") or None

            conn.execute(
                "INSERT OR REPLACE INTO lib_playlists "
                "(id, name, source_platform, cover_url, track_count, "
                "duration_ms, updated_at, synced_at) "
                "VALUES (?, ?, 'navidrome', ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (pl_id, pl_name, cover_url, track_count, duration_ms, changed),
            )

            # Re-fetch track list (individual call per playlist)
            try:
                songs = client.get_playlist_songs(pl_id)
            except Exception as exc:
                logger.warning(
                    "library_playlists_service: failed to get songs for playlist %s: %s",
                    pl_id, exc,
                )
                songs = []

            # Delete old positions then re-insert
            conn.execute(
                "DELETE FROM lib_playlist_tracks WHERE playlist_id = ?",
                (pl_id,),
            )
            for pos, song in enumerate(songs):
                song_id = song.get("id")
                if not song_id:
                    continue
                # Only insert if the track exists in lib_tracks
                row = conn.execute(
                    "SELECT id FROM lib_tracks WHERE id = ? LIMIT 1",
                    (song_id,),
                ).fetchone()
                if row:
                    conn.execute(
                        "INSERT INTO lib_playlist_tracks "
                        "(playlist_id, track_id, position) VALUES (?, ?, ?)",
                        (pl_id, song_id, pos),
                    )
                    tracks_synced += 1

            playlists_synced += 1

    logger.info(
        "library_playlists_service.sync [navidrome]: %d playlists, %d tracks",
        playlists_synced, tracks_synced,
    )
    return {"playlists_synced": playlists_synced, "tracks_synced": tracks_synced}


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------

def _sync_plex() -> dict:
    if not config.PLEX_URL or not config.PLEX_TOKEN:
        raise ValueError("PLEX_URL and PLEX_TOKEN must be set for Plex playlist sync")

    from plexapi.server import PlexServer

    plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)

    playlists_synced = 0
    tracks_synced = 0

    with _connect() as conn:
        for pl in plex.playlists():
            # Only audio playlists
            if getattr(pl, "playlistType", None) != "audio":
                continue

            pl_id = str(pl.ratingKey)
            pl_name = pl.title or ""
            cover_url = None
            updated_at = str(pl.updatedAt) if pl.updatedAt else None

            items = []
            try:
                items = pl.items()
            except Exception as exc:
                logger.warning(
                    "library_playlists_service: failed to get items for Plex playlist %s: %s",
                    pl_id, exc,
                )

            track_count = len(items)
            duration_ms = sum(
                int(getattr(t, "duration", 0) or 0) for t in items
            )

            conn.execute(
                "INSERT OR REPLACE INTO lib_playlists "
                "(id, name, source_platform, cover_url, track_count, "
                "duration_ms, updated_at, synced_at) "
                "VALUES (?, ?, 'plex', ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (pl_id, pl_name, cover_url, track_count, duration_ms, updated_at),
            )

            conn.execute(
                "DELETE FROM lib_playlist_tracks WHERE playlist_id = ?",
                (pl_id,),
            )
            for pos, track in enumerate(items):
                track_id = str(track.ratingKey)
                row = conn.execute(
                    "SELECT id FROM lib_tracks WHERE id = ? LIMIT 1",
                    (track_id,),
                ).fetchone()
                if row:
                    conn.execute(
                        "INSERT INTO lib_playlist_tracks "
                        "(playlist_id, track_id, position) VALUES (?, ?, ?)",
                        (pl_id, track_id, pos),
                    )
                    tracks_synced += 1

            playlists_synced += 1

    logger.info(
        "library_playlists_service.sync [plex]: %d playlists, %d tracks",
        playlists_synced, tracks_synced,
    )
    return {"playlists_synced": playlists_synced, "tracks_synced": tracks_synced}


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

def rename_playlist(playlist_id: str, new_name: str) -> None:
    """Rename a playlist on the platform and update lib_playlists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT source_platform FROM lib_playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Playlist not found: {playlist_id}")
        platform = row["source_platform"]

    if platform == "navidrome":
        from app.db.navidrome_reader import _get_client
        client = _get_client()
        client.rename_playlist(playlist_id, new_name)
    elif platform == "plex":
        if not config.PLEX_URL or not config.PLEX_TOKEN:
            raise ValueError("PLEX_URL and PLEX_TOKEN must be set")
        from plexapi.server import PlexServer
        plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)
        pl = plex.fetchItem(int(playlist_id))
        pl.edit(title=new_name)
    else:
        raise ValueError(f"Unsupported platform for rename: {platform}")

    with _connect() as conn:
        conn.execute(
            "UPDATE lib_playlists SET name = ?, synced_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (new_name, playlist_id),
        )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_playlist(playlist_id: str) -> None:
    """Delete a playlist from the platform and from lib_playlists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT source_platform FROM lib_playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Playlist not found: {playlist_id}")
        platform = row["source_platform"]

    if platform == "navidrome":
        from app.db.navidrome_reader import _get_client
        client = _get_client()
        client.delete_playlist(playlist_id)
    elif platform == "plex":
        if not config.PLEX_URL or not config.PLEX_TOKEN:
            raise ValueError("PLEX_URL and PLEX_TOKEN must be set")
        from plexapi.server import PlexServer
        plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)
        pl = plex.fetchItem(int(playlist_id))
        pl.delete()
    else:
        raise ValueError(f"Unsupported platform for delete: {platform}")

    # ON DELETE CASCADE removes lib_playlist_tracks rows automatically
    with _connect() as conn:
        conn.execute(
            "DELETE FROM lib_playlists WHERE id = ?",
            (playlist_id,),
        )
