"""
cc_store.py — rythmx's own SQLite database (cc.db).

All tables owned by rythmx. Never touches SoulSync's DB.
All queries use parameterized form only.
"""
import sqlite3
import logging
from app import config

logger = logging.getLogger(__name__)


def _connect():
    conn = sqlite3.connect(config.CC_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate_add_column(table: str, column: str, col_type: str):
    """Add a column to an existing table if it doesn't already exist. No-op otherwise."""
    try:
        with _connect() as conn:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass  # table may not exist yet on first run — CREATE TABLE in init_db handles it


def init_db():
    """Create all cc.db tables if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cc_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                source TEXT,
                score REAL,
                acquisition_status TEXT,
                reason TEXT,
                cycle_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cc_playlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_name TEXT DEFAULT 'For You',
                track_id TEXT,
                spotify_track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_cover_url TEXT,
                score REAL,
                position INT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                plex_playlist_id TEXT,
                UNIQUE(track_id)
            );

            CREATE TABLE IF NOT EXISTS cc_taste_cache (
                artist_name TEXT PRIMARY KEY,
                play_count INT,
                period TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cc_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS artist_identity_cache (
                lastfm_name TEXT PRIMARY KEY,
                deezer_artist_id TEXT,
                spotify_artist_id TEXT,
                itunes_artist_id TEXT,
                mb_artist_id TEXT,
                soulsync_artist_id TEXT,
                confidence INTEGER DEFAULT 80,
                last_resolved_ts INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cc_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_track_id TEXT UNIQUE,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_cover_url TEXT,
                score REAL,
                is_owned INTEGER DEFAULT 0,
                plex_rating_key TEXT,
                source TEXT,
                scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS playlists (
                name TEXT PRIMARY KEY,
                source TEXT DEFAULT 'manual',
                source_url TEXT,
                auto_sync INTEGER DEFAULT 0,
                mode TEXT DEFAULT 'library_only',
                last_synced_ts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    # Migrations: add columns that may not exist in older cc.db files
    _migrate_add_column("artist_identity_cache", "itunes_artist_id", "TEXT")
    _migrate_add_column("artist_identity_cache", "soulsync_artist_id", "TEXT")
    _migrate_add_column("artist_identity_cache", "resolution_method", "TEXT")
    _migrate_add_column("playlists", "max_tracks", "INTEGER DEFAULT 50")

    logger.info("cc.db initialized at %s", config.CC_DB)


# --- Settings ---

def get_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM cc_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cc_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def get_all_settings() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM cc_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# --- History ---

def add_history_entry(track: dict, status: str, reason: str = ""):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cc_history
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
            "SELECT * FROM cc_history ORDER BY cycle_date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def is_release_in_history(artist_name: str, album_name: str) -> bool:
    """
    Return True if this artist+album was already identified or queued in a previous cycle.
    Used to prevent re-adding the same unowned release every run.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM cc_history
               WHERE lower(artist_name) = lower(?)
               AND lower(album_name) = lower(?)
               AND acquisition_status IN ('identified', 'queued', 'success')
               LIMIT 1""",
            (artist_name, album_name)
        ).fetchone()
        return row is not None


def get_history_summary() -> dict:
    with _connect() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN acquisition_status = 'queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN acquisition_status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN acquisition_status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN acquisition_status = 'skipped' THEN 1 ELSE 0 END) as skipped
            FROM cc_history
        """).fetchone()
        return dict(row) if row else {}


# --- Playlist ---

def save_playlist(tracks: list[dict], playlist_name: str = "For You"):
    """Replace the current playlist with a new scored track list."""
    with _connect() as conn:
        conn.execute("DELETE FROM cc_playlist WHERE playlist_name = ?", (playlist_name,))
        for i, t in enumerate(tracks):
            conn.execute(
                """INSERT OR REPLACE INTO cc_playlist
                   (playlist_name, track_id, spotify_track_id, track_name, artist_name,
                    album_name, album_cover_url, score, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                )
            )


def get_playlist(playlist_name: str = "For You") -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cc_playlist WHERE playlist_name = ? ORDER BY position ASC",
            (playlist_name,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_to_playlist(track: dict, playlist_name: str = "For You"):
    """Append a single track to the playlist (upsert by track_id, ignores duplicates)."""
    with _connect() as conn:
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM cc_playlist WHERE playlist_name = ?",
            (playlist_name,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO cc_playlist
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
            "DELETE FROM cc_playlist WHERE track_id = ? AND playlist_name = ?",
            (track_id, playlist_name)
        )


def update_playlist_plex_id(playlist_name: str, plex_playlist_id: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE cc_playlist SET plex_playlist_id = ? WHERE playlist_name = ?",
            (plex_playlist_id, playlist_name)
        )


# --- Artist identity cache ---

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


# --- Taste cache ---

def upsert_taste_cache(artist_name: str, play_count: int, period: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cc_taste_cache (artist_name, play_count, period)
               VALUES (?, ?, ?)
               ON CONFLICT(artist_name) DO UPDATE SET
                   play_count = excluded.play_count,
                   period = excluded.period,
                   last_updated = CURRENT_TIMESTAMP""",
            (artist_name, play_count, period)
        )


def get_taste_cache() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT artist_name, play_count FROM cc_taste_cache").fetchall()
        return {r["artist_name"]: r["play_count"] for r in rows}


# --- Maintenance ---

def clear_history():
    """Delete all rows from cc_history."""
    with _connect() as conn:
        conn.execute("DELETE FROM cc_history")


def reset_db():
    """Wipe all user data tables. Schema is preserved (re-created by init_db on next start)."""
    with _connect() as conn:
        conn.executescript("""
            DELETE FROM cc_history;
            DELETE FROM cc_playlist;
            DELETE FROM cc_taste_cache;
            DELETE FROM cc_settings;
            DELETE FROM artist_identity_cache;
            DELETE FROM cc_candidates;
            DELETE FROM playlists;
        """)
    logger.info("cc.db reset — all user data cleared")


# --- Playlist metadata (multi-playlist management) ---

def create_playlist_meta(name: str, source: str = "manual", source_url: str = None,
                         auto_sync: bool = False, mode: str = "library_only",
                         max_tracks: int = 50):
    """Create a playlist metadata entry. No-op if the name already exists."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO playlists (name, source, source_url, auto_sync, mode, max_tracks)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, source, source_url, 1 if auto_sync else 0, mode, max_tracks)
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
    Surfaces playlists that have tracks in cc_playlist even without a metadata row
    (e.g. the legacy 'For You' playlist created by the CC pipeline).
    """
    with _connect() as conn:
        # Pre-aggregate track counts from cc_playlist
        agg_rows = conn.execute("""
            SELECT playlist_name,
                   COUNT(*) AS track_count,
                   SUM(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END) AS owned_count
            FROM cc_playlist
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
        conn.execute("DELETE FROM cc_playlist WHERE playlist_name = ?", (name,))
