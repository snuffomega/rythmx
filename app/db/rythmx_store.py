"""
rythmx_store.py — rythmx's own SQLite database (rythmx.db).

All tables owned by rythmx. Never touches SoulSync's DB.
All queries use parameterized form only.
"""
import sqlite3
import logging
from app import config

logger = logging.getLogger(__name__)


def _connect():
    conn = sqlite3.connect(config.RYTHMX_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn



_TRIGGER_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_lib_releases_artistid_insert
BEFORE INSERT ON lib_releases
WHEN NEW.artist_id IS NULL
BEGIN
    INSERT INTO fk_violation_log(table_name, op, row_id, payload)
    VALUES ('lib_releases', 'INSERT_NULL_ARTIST', NEW.id,
            json_object('id', NEW.id, 'artist_name', NEW.artist_name));
    SELECT RAISE(ABORT, 'lib_releases.artist_id must not be NULL');
END;

CREATE TRIGGER IF NOT EXISTS trg_lib_releases_artistid_update
BEFORE UPDATE ON lib_releases
WHEN NEW.artist_id IS NULL
BEGIN
    INSERT INTO fk_violation_log(table_name, op, row_id, payload)
    VALUES ('lib_releases', 'UPDATE_NULL_ARTIST', NEW.id,
            json_object('id', NEW.id, 'artist_name', NEW.artist_name));
    SELECT RAISE(ABORT, 'lib_releases.artist_id must not be NULL on UPDATE');
END;
"""


def init_db():
    """Create all rythmx.db tables if they don't exist. Safe to call on every startup.

    The genesis migration (000_genesis.sql) creates ALL tables and indexes.
    init_db() only runs migrations and performs post-schema maintenance.
    """
    from migrations.runner import run_pending_migrations
    run_pending_migrations(config.RYTHMX_DB)

    with _connect() as conn:
        # Apply enforcement triggers via executescript() — the migration runner
        # cannot handle multi-statement DDL (splits on semicolons).
        conn.executescript(_TRIGGER_DDL)
        # Prune stale image cache entries (> 30 days since last access)
        conn.execute("DELETE FROM image_cache WHERE last_accessed < datetime('now', '-30 days')")

    logger.info("rythmx.db initialized at %s", config.RYTHMX_DB)

    # After schema is ready, clean up secondary catalog rows if single-catalog mode
    ensure_single_catalog_cleanup()


# --- API Key ---

def get_api_key() -> str | None:
    """Return the active API key, or None if not yet generated."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT key FROM api_keys ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["key"] if row else None


def _set_api_key(key: str) -> None:
    """Replace the active API key (internal — callers use generate_new_api_key)."""
    with _connect() as conn:
        conn.execute("DELETE FROM api_keys")
        conn.execute("INSERT INTO api_keys (key) VALUES (?)", (key,))


def generate_new_api_key() -> str:
    """Generate a cryptographically random 64-char hex API key, persist it, and return it."""
    import secrets
    key = secrets.token_hex(32)
    _set_api_key(key)
    return key


# --- Image Cache ---

def get_image_cache(entity_type: str, entity_key: str) -> str | None:
    """Return cached image URL or None if not cached / empty.

    Empty strings are treated as misses so stale 'not found' entries from
    failed fetches are retried rather than permanently suppressed.
    """
    with _connect() as conn:
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


def set_image_cache(entity_type: str, entity_key: str, image_url: str):
    """Upsert an image URL into the cache."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO image_cache (entity_type, entity_key, image_url, last_accessed)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(entity_type, entity_key) DO UPDATE SET
                   image_url=excluded.image_url,
                   last_accessed=datetime('now')""",
            (entity_type, entity_key, image_url)
        )


def clear_image_cache():
    """Delete all rows from image_cache."""
    with _connect() as conn:
        conn.execute("DELETE FROM image_cache")


# --- Settings ---

def get_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def get_all_settings() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# --- History ---

def add_history_entry(track: dict, status: str, reason: str = ""):
    with _connect() as conn:
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
            )
        )


def get_history(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY cycle_date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def is_release_in_history(artist_name: str, album_name: str) -> bool:
    """
    Return True if this artist+album was already identified or queued in a previous cycle.
    Used to prevent re-adding the same unowned release every run.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM history
               WHERE lower(artist_name) = lower(?)
               AND lower(album_name) = lower(?)
               AND acquisition_status IN ('identified', 'queued', 'success')
               LIMIT 1""",
            (artist_name, album_name)
        ).fetchone()
        return row is not None


# --- Download queue ---

def is_in_queue(artist_name: str, album_title: str) -> bool:
    """
    Return True if this release has a pending or submitted acquisition request.
    Does NOT block 'found', 'failed', or 'skipped' — those can be re-evaluated.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM download_queue
               WHERE lower(artist_name) = lower(?)
               AND lower(album_title) = lower(?)
               AND status IN ('pending', 'submitted')
               LIMIT 1""",
            (artist_name, album_title)
        ).fetchone()
        return row is not None


def add_to_queue(artist_name: str, album_title: str, release_date: str = None,
                 kind: str = None, source: str = None,
                 itunes_album_id: str = None, deezer_album_id: str = None,
                 spotify_album_id: str = None,
                 requested_by: str = "cc", playlist_name: str = None) -> int:
    """
    Insert a release into the download queue. UNIQUE(artist_name, album_title) —
    if an entry already exists (any status), the existing row is left unchanged and
    its id is returned.
    """
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO download_queue
               (artist_name, album_title, release_date, kind, source,
                itunes_album_id, deezer_album_id, spotify_album_id,
                requested_by, playlist_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (artist_name, album_title, release_date, kind, source,
             itunes_album_id or None, deezer_album_id or None, spotify_album_id or None,
             requested_by, playlist_name)
        )
        row = conn.execute(
            "SELECT id FROM download_queue WHERE lower(artist_name)=lower(?) AND lower(album_title)=lower(?)",
            (artist_name, album_title)
        ).fetchone()
        return row["id"] if row else -1


def get_queue(status: str = None, playlist_name: str = None) -> list[dict]:
    """Return queue rows, optionally filtered by status and/or playlist_name."""
    with _connect() as conn:
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


def update_queue_status(queue_id: int, status: str, provider_response: str = None):
    """Update status and updated_at for a queue row."""
    with _connect() as conn:
        conn.execute(
            """UPDATE download_queue
               SET status = ?, provider_response = COALESCE(?, provider_response),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, provider_response, queue_id)
        )


def get_queue_stats() -> dict:
    """Return counts by status."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM download_queue GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        return {
            "pending":   stats.get("pending", 0),
            "submitted": stats.get("submitted", 0),
            "found":     stats.get("found", 0),
            "failed":    stats.get("failed", 0),
            "skipped":   stats.get("skipped", 0),
            "total":     sum(stats.values()),
        }


def get_queue_status(artist_name: str, album_title: str) -> str | None:
    """Return the most recent download_queue status for artist+album, or None if not queued."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT status FROM download_queue
               WHERE lower(artist_name) = lower(?) AND lower(album_title) = lower(?)
               ORDER BY created_at DESC LIMIT 1""",
            (artist_name, album_title)
        ).fetchone()
        return row["status"] if row else None


def get_history_summary() -> dict:
    with _connect() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN acquisition_status = 'queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN acquisition_status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN acquisition_status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN acquisition_status = 'skipped' THEN 1 ELSE 0 END) as skipped
            FROM history
        """).fetchone()
        return dict(row) if row else {}


# --- Playlist ---

def save_playlist(tracks: list[dict], playlist_name: str = "For You"):
    """Replace the current playlist with a new scored track list."""
    with _connect() as conn:
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
                )
            )


def get_playlist(playlist_name: str = "For You") -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM playlist_tracks WHERE playlist_name = ? ORDER BY position ASC",
            (playlist_name,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_to_playlist(track: dict, playlist_name: str = "For You"):
    """Append a single track to the playlist (upsert by track_id, ignores duplicates)."""
    with _connect() as conn:
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_tracks WHERE playlist_name = ?",
            (playlist_name,)
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
            )
        )


def remove_from_playlist(track_id: str, playlist_name: str = "For You"):
    """Remove a track from the playlist by track_id."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM playlist_tracks WHERE track_id = ? AND playlist_name = ?",
            (track_id, playlist_name)
        )


def update_playlist_plex_id(playlist_name: str, plex_playlist_id: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE playlist_tracks SET plex_playlist_id = ? WHERE playlist_name = ?",
            (plex_playlist_id, playlist_name)
        )


# --- Artist identity cache ---

def get_lib_artist_ids(artist_name: str) -> dict | None:
    """
    CC-1 DB-first lookup: return stored provider IDs from lib_artists for an artist name.
    Returns {itunes_artist_id, deezer_artist_id, spotify_artist_id, lastfm_mbid,
             match_confidence} or None if artist not found in lib_artists.
    Used by identity_resolver.resolve_artist() before any API call.
    """
    try:
        with _connect() as conn:
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


def get_cached_artist(lastfm_name: str) -> dict | None:
    """Return cached provider IDs for a Last.fm artist name, or None if not cached."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM artist_identity_cache WHERE lastfm_name = ?", (lastfm_name,)
        ).fetchone()
        return dict(row) if row else None


def cache_artist(lastfm_name: str, deezer_artist_id: str = None,
                 spotify_artist_id: str = None, itunes_artist_id: str = None,
                 mb_artist_id: str = None, soulsync_artist_id: str = None,
                 confidence: int = 80, resolution_method: str = None):
    """Upsert provider IDs for a Last.fm artist name.

    Uses COALESCE so a new None value never overwrites an existing good ID.
    soulsync_artist_id is the SoulSync internal artists.id — enables exact PK
    joins for owned-check instead of fuzzy text matching.
    resolution_method: how identity was confirmed (name_only / track_overlap_N / cache_hit).
    """
    import time
    with _connect() as conn:
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
            (lastfm_name, deezer_artist_id, spotify_artist_id, itunes_artist_id,
             mb_artist_id, soulsync_artist_id, confidence, resolution_method, int(time.time()))
        )


def get_artist_navidrome_cover(artist_name: str) -> str | None:
    """Return the Navidrome coverArt ID for an artist (used by image_service)."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT thumb_url_navidrome FROM lib_artists "
                "WHERE name_lower = lower(?) LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


# --- Taste cache ---

def upsert_taste_cache(artist_name: str, play_count: int, period: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO taste_cache (artist_name, play_count, period)
               VALUES (?, ?, ?)
               ON CONFLICT(artist_name) DO UPDATE SET
                   play_count = excluded.play_count,
                   period = excluded.period,
                   last_updated = CURRENT_TIMESTAMP""",
            (artist_name, play_count, period)
        )


def get_taste_cache() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT artist_name, play_count FROM taste_cache").fetchall()
        return {r["artist_name"]: r["play_count"] for r in rows}


# --- Maintenance ---

def clear_history():
    """Delete all rows from history."""
    with _connect() as conn:
        conn.execute("DELETE FROM history")


def reset_db():
    """Wipe all user data tables. Schema is preserved (re-created by init_db on next start)."""
    with _connect() as conn:
        conn.executescript("""
            DELETE FROM history;
            DELETE FROM playlist_tracks;
            DELETE FROM taste_cache;
            DELETE FROM app_settings;
            DELETE FROM artist_identity_cache;
            DELETE FROM candidates;
            DELETE FROM playlists;
            DELETE FROM download_queue;
        """)
    logger.info("rythmx.db reset — all user data cleared")


# --- Playlist metadata (multi-playlist management) ---

def create_playlist_meta(name: str, source: str = "manual", source_url: str = None,
                         auto_sync: bool = False, mode: str = "library_only",
                         max_tracks: int = 50):
    """Create a playlist metadata entry. Updates source/mode if called with source='new_music'."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO playlists (name, source, source_url, auto_sync, mode, max_tracks)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, source, source_url, 1 if auto_sync else 0, mode, max_tracks)
        )
        # CC-generated playlists always refresh their source/mode so stale values don't persist
        if source == "new_music":
            conn.execute(
                "UPDATE playlists SET source=?, mode=? WHERE name=?",
                (source, mode, name)
            )


def get_playlist_meta(name: str) -> dict | None:
    """Return metadata for a single playlist, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM playlists WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def update_playlist_meta(name: str, auto_sync: bool = None, mode: str = None,
                         source_url: str = None, max_tracks: int = None):
    """Update mutable fields on an existing playlist metadata row."""
    with _connect() as conn:
        if auto_sync is not None:
            conn.execute(
                "UPDATE playlists SET auto_sync = ? WHERE name = ?",
                (1 if auto_sync else 0, name)
            )
        if mode is not None:
            conn.execute(
                "UPDATE playlists SET mode = ? WHERE name = ?",
                (mode, name)
            )
        if source_url is not None:
            conn.execute(
                "UPDATE playlists SET source_url = ? WHERE name = ?",
                (source_url, name)
            )
        if max_tracks is not None:
            conn.execute(
                "UPDATE playlists SET max_tracks = ? WHERE name = ?",
                (int(max_tracks), name)
            )


def mark_playlist_synced(name: str):
    """Update last_synced_ts to now for a playlist."""
    import time
    with _connect() as conn:
        conn.execute(
            "UPDATE playlists SET last_synced_ts = ? WHERE name = ?",
            (int(time.time()), name)
        )


def list_playlists() -> list[dict]:
    """
    Return all playlists with track/owned counts.
    Surfaces playlists that have tracks in playlist_tracks even without a metadata row
    (e.g. the legacy 'For You' playlist created by the CC pipeline).
    """
    with _connect() as conn:
        # Pre-aggregate track counts from playlist_tracks
        agg_rows = conn.execute("""
            SELECT playlist_name,
                   COUNT(*) AS track_count,
                   SUM(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END) AS owned_count
            FROM playlist_tracks
            GROUP BY playlist_name
        """).fetchall()
        agg = {r["playlist_name"]: dict(r) for r in agg_rows}

        meta_rows = conn.execute(
            "SELECT * FROM playlists ORDER BY created_at DESC"
        ).fetchall()
        meta = {r["name"]: dict(r) for r in meta_rows}

        # Merge: start with all names from both tables
        all_names = set(agg.keys()) | set(meta.keys())
        result = []
        for name in all_names:
            m = meta.get(name, {})
            a = agg.get(name, {"track_count": 0, "owned_count": 0})
            result.append({
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
            })
        result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return result


def delete_playlist(name: str):
    """Delete a playlist and all its tracks."""
    with _connect() as conn:
        conn.execute("DELETE FROM playlists WHERE name = ?", (name,))
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_name = ?", (name,))


def rename_playlist(old_name: str, new_name: str):
    """Rename a playlist in both the playlists metadata table and playlist_tracks rows."""
    with _connect() as conn:
        conn.execute("UPDATE playlists SET name = ? WHERE name = ?", (new_name, old_name))
        conn.execute(
            "UPDATE playlist_tracks SET playlist_name = ? WHERE playlist_name = ?",
            (new_name, old_name)
        )


def remove_playlist_row(row_id: int):
    """Remove a single track row from playlist_tracks by its primary key id."""
    with _connect() as conn:
        conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (row_id,))


def get_release_itunes_album_id(artist_name: str, album_title: str) -> str | None:
    """
    Return itunes_album_id for a known artist+album, or None.
    Used by image_service as Tier 0 for album art lookups.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT itunes_album_id FROM lib_releases
               WHERE artist_name_lower = lower(?)
                 AND title_lower = lower(?)
                 AND itunes_album_id IS NOT NULL
               LIMIT 1""",
            (artist_name, album_title),
        ).fetchone()
    return row["itunes_album_id"] if row else None


def get_missing_image_entities(limit: int = 40) -> list[tuple[str, str, str]]:
    """
    Return up to `limit` (entity_type, name, artist) tuples for entities that
    have no resolved image in image_cache. Albums from playlist_tracks are checked
    first (most visible); artist images from artist_identity_cache fill the rest.

    Only entities with a completely absent or empty image_url are returned —
    entries with non-empty URLs are skipped (already cached).
    """
    with _connect() as conn:
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


def backfill_normalized_titles() -> int:
    """Populate normalized_title and version_type for all lib_releases rows missing them."""
    from app.services.enrichment._helpers import detect_version_type
    from app.clients.music_client import norm

    updated = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM lib_releases WHERE normalized_title IS NULL"
        ).fetchall()
        for row in rows:
            cleaned_title, version_type = detect_version_type(row["title"])
            normalized_title = norm(cleaned_title)
            conn.execute(
                "UPDATE lib_releases SET normalized_title = ?, version_type = ? WHERE id = ?",
                (normalized_title, version_type, row["id"]),
            )
            updated += 1
    logger.info("Backfilled normalized_title for %d lib_releases rows", updated)
    return updated


def recompute_normalized_titles(artist_ids: list[str] | None = None) -> int:
    """Recompute normalized_title and version_type for lib_releases rows.

    Unlike backfill_normalized_titles() which skips rows where normalized_title
    is already set, this overwrites all rows. Use after updating the title
    normalization regex so existing rows pick up the new logic.

    Args:
        artist_ids: If provided, only recompute for these artists. Otherwise all.

    Returns count of rows updated.
    """
    from app.services.enrichment._helpers import detect_version_type
    from app.clients.music_client import norm

    updated = 0
    with _connect() as conn:
        if artist_ids:
            placeholders = ",".join("?" * len(artist_ids))
            rows = conn.execute(
                f"SELECT id, title FROM lib_releases WHERE artist_id IN ({placeholders})",
                artist_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, title FROM lib_releases").fetchall()
        for row in rows:
            cleaned_title, version_type = detect_version_type(row["title"])
            normalized_title = norm(cleaned_title)
            conn.execute(
                "UPDATE lib_releases SET normalized_title = ?, version_type = ? WHERE id = ?",
                (normalized_title, version_type, row["id"]),
            )
            updated += 1
    logger.info("recompute_normalized_titles: reprocessed %d lib_releases rows%s",
                updated, f" (scoped to {len(artist_ids)} artists)" if artist_ids else "")
    return updated


def refresh_missing_counts(artist_id: str | None = None,
                           artist_ids: list[str] | None = None) -> int:
    """Recompute lib_artists.missing_count from lib_releases with full dedup logic.

    Mirrors the ROW_NUMBER dedup in library_browse.library_artist_detail():
      - resolved_kind via COALESCE(kind_deezer, kind_itunes, track_count heuristic)
      - source priority: deezer > itunes
      - singles suppressed when album/ep exists with same normalized_title

    Args:
        artist_id: If provided, refresh only this artist.
        artist_ids: If provided, refresh only these artists (takes precedence over artist_id).
        If neither provided, refresh all.

    Returns number of artists updated.
    """
    if artist_ids:
        placeholders = ",".join("?" * len(artist_ids))
        scope_clause = f"WHERE lib_artists.id IN ({placeholders})"
        params: tuple = tuple(artist_ids)
    elif artist_id:
        scope_clause = "WHERE lib_artists.id = ?"
        params = (artist_id,)
    else:
        scope_clause = ""
        params = ()

    with _connect() as conn:
        # Step 1: Compute deduplicated missing count per artist
        conn.execute(f"""
            UPDATE lib_artists SET missing_count = COALESCE((
                SELECT COUNT(*) FROM (
                    SELECT id,
                           artist_id,
                           artist_name_lower,
                           normalized_title,
                           COALESCE(
                               kind_deezer, kind_itunes,
                               CASE
                                   WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                   WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                   ELSE 'album'
                               END
                           ) AS resolved_kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY artist_name_lower, normalized_title,
                                            COALESCE(
                                                kind_deezer, kind_itunes,
                                                CASE
                                                    WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                                    WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                                    ELSE 'album'
                                                END
                                            )
                               ORDER BY
                                   CASE catalog_source WHEN 'deezer' THEN 1 WHEN 'itunes' THEN 2 ELSE 3 END,
                                   COALESCE(thumb_url_deezer, thumb_url_itunes) IS NOT NULL DESC,
                                   COALESCE(release_date_itunes, release_date_deezer) IS NOT NULL DESC
                           ) AS rn
                    FROM lib_releases
                    WHERE artist_id = lib_artists.id
                      AND is_owned = 0
                      AND user_dismissed = 0
                ) deduped
                WHERE rn = 1
                  AND NOT (
                      resolved_kind = 'single'
                      AND EXISTS (
                          SELECT 1 FROM lib_releases lr2
                          WHERE lr2.artist_id = deduped.artist_id
                            AND lr2.normalized_title = deduped.normalized_title
                            AND COALESCE(lr2.kind_deezer, lr2.kind_itunes, 'album') IN ('album', 'ep')
                            AND lr2.id != deduped.id
                      )
                  )
            ), 0)
            {scope_clause}
        """, params)
        updated = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    scope_msg = ""
    if artist_ids:
        scope_msg = f" (scoped to {len(artist_ids)} artists)"
    elif artist_id:
        scope_msg = f" (artist_id={artist_id})"
    logger.info("refresh_missing_counts: updated %d artists%s", updated, scope_msg)
    return updated


def populate_canonical_release_ids(artist_id: str | None = None,
                                   artist_ids: list[str] | None = None) -> int:
    """Assign canonical_release_id to lib_releases rows.

    Groups by (artist_id, normalized_title). The "primary" release per group is
    chosen by: is_owned DESC, version_type='original' first, release_date ASC,
    deezer source preferred. All group members share the primary's id.

    Args:
        artist_id: If provided, only refresh rows for this artist.
        artist_ids: If provided, only refresh rows for these artists (takes precedence).
        If neither provided, refresh all rows.

    Returns count of rows updated.
    """
    where = "WHERE normalized_title IS NOT NULL AND artist_id IS NOT NULL"
    params: tuple = ()
    if artist_ids:
        placeholders = ",".join("?" * len(artist_ids))
        where += f" AND artist_id IN ({placeholders})"
        params = tuple(artist_ids)
    elif artist_id:
        where += " AND artist_id = ?"
        params = (artist_id,)

    sub_where = where  # same filter for the correlated subquery

    with _connect() as conn:
        cursor = conn.execute(
            f"""
            UPDATE lib_releases SET canonical_release_id = (
                SELECT sub.id FROM lib_releases sub
                WHERE sub.artist_id = lib_releases.artist_id
                  AND sub.normalized_title = lib_releases.normalized_title
                  AND sub.normalized_title IS NOT NULL
                ORDER BY
                    sub.is_owned DESC,
                    CASE sub.version_type WHEN 'original' THEN 0 ELSE 1 END,
                    COALESCE(sub.release_date_itunes, sub.release_date_deezer) ASC,
                    CASE sub.catalog_source WHEN 'deezer' THEN 1 ELSE 2 END
                LIMIT 1
            )
            {where}
            """,
            params,
        )
        updated = cursor.rowcount

    scope_label = f"{len(artist_ids)} artists" if artist_ids else (artist_id or "all")
    logger.info("populate_canonical_release_ids: updated %d rows (scope=%s)", updated, scope_label)
    return updated


def ensure_single_catalog_cleanup():
    """One-time cleanup: remove secondary-source rows from lib_releases.

    Reads CATALOG_PRIMARY from config, deletes rows from the other source,
    and resets derived columns so they are recalculated on the next pipeline run.
    Idempotent: gated by app_settings flag. Re-runs if CATALOG_PRIMARY changes.
    """
    primary = config.CATALOG_PRIMARY
    secondary = "itunes" if primary == "deezer" else "deezer"

    with _connect() as conn:
        done = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'single_catalog_done'"
        ).fetchone()
        if done and done[0] == primary:
            return  # already cleaned for this primary source

        deleted = conn.execute(
            "DELETE FROM lib_releases WHERE catalog_source = ?",
            (secondary,),
        ).rowcount

        # Reset derived columns — forces recalculation by next pipeline run
        conn.execute(
            "UPDATE lib_releases SET canonical_release_id = NULL, "
            "is_owned = 0, owned_checked_at = NULL"
        )

        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("single_catalog_done", primary),
        )

    logger.info(
        "ensure_single_catalog_cleanup: deleted %d %s rows, primary=%s",
        deleted, secondary, primary,
    )


# ---------------------------------------------------------------------------
# Pipeline history
# ---------------------------------------------------------------------------

def insert_pipeline_run(
    pipeline_type: str,
    run_mode: str,
    config_snapshot: dict,
    triggered_by: str = "manual",
) -> int:
    """Insert a new pipeline_history row at run start. Returns the new row id."""
    import json as _json
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO pipeline_history
               (pipeline_type, run_mode, status, config_json, triggered_by)
               VALUES (?, ?, 'running', ?, ?)""",
            (pipeline_type, run_mode, _json.dumps(config_snapshot), triggered_by),
        )
        return cur.lastrowid


def complete_pipeline_run(
    run_id: int,
    summary: dict,
    error_message: str | None = None,
) -> None:
    """Mark a pipeline_history row as completed (or error) with duration and summary."""
    import json as _json
    status = "error" if error_message else "completed"
    with _connect() as conn:
        conn.execute(
            """UPDATE pipeline_history
               SET status = ?,
                   finished_at = CURRENT_TIMESTAMP,
                   run_duration = (julianday(CURRENT_TIMESTAMP)
                                   - julianday(started_at)) * 86400,
                   summary_json = ?,
                   error_message = ?
               WHERE id = ?""",
            (status, _json.dumps(summary), error_message, run_id),
        )


def get_pipeline_runs(
    pipeline_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent pipeline_history rows, optionally filtered by pipeline_type."""
    with _connect() as conn:
        if pipeline_type:
            rows = conn.execute(
                """SELECT * FROM pipeline_history
                   WHERE pipeline_type = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (pipeline_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM pipeline_history
                   ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
