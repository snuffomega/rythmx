"""
plex_reader.py — Plex-native library backend.

Implements the library reader interface (see soulsync_reader.py for the contract).
Reads from a local library.db built and maintained by sync_library().

sync_library() walks the Plex music library via python-plexapi and captures
ratingKeys directly — the same mechanism SoulSync uses. All other functions
query library.db only; no network calls outside of sync_library().

Functions not applicable to this backend return safe empty values:
  get_discovery_pool()       → []    (SoulSync-specific)
  get_similar_artists_map()  → {}    (SoulSync-specific)
  get_soulsync_artist_id()   → None  (not applicable)

library.db lives alongside cc.db in the LIBRARY_DB path (/data/cc/library.db).
It is a derived cache — safe to delete and re-sync at any time.
"""
import sqlite3
import time
import logging
from app import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _connect():
    """Return a WAL-mode connection to library.db (read/write, rythmx-owned)."""
    conn = sqlite3.connect(config.LIBRARY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _ensure_tables():
    """Create library.db schema if not present. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS artists (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                name_lower  TEXT NOT NULL,
                spotify_artist_id  TEXT,
                itunes_artist_id   TEXT,
                deezer_id          TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_artists_name_lower ON artists(name_lower);

            CREATE TABLE IF NOT EXISTS albums (
                id           TEXT PRIMARY KEY,
                artist_id    TEXT NOT NULL,
                title        TEXT NOT NULL,
                title_lower  TEXT NOT NULL,
                year         INTEGER,
                record_type  TEXT,
                thumb_url    TEXT,
                spotify_album_id  TEXT,
                itunes_album_id   TEXT,
                deezer_id         TEXT,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_albums_artist_title
                ON albums(artist_id, title_lower);

            CREATE TABLE IF NOT EXISTS tracks (
                id           TEXT PRIMARY KEY,
                album_id     TEXT NOT NULL,
                artist_id    TEXT NOT NULL,
                title        TEXT NOT NULL,
                title_lower  TEXT NOT NULL,
                track_number INTEGER,
                duration     INTEGER,
                file_path    TEXT,
                spotify_track_id TEXT,
                deezer_id        TEXT,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tracks_spotify ON tracks(spotify_track_id);
            CREATE INDEX IF NOT EXISTS idx_tracks_deezer  ON tracks(deezer_id);

            CREATE TABLE IF NOT EXISTS library_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_library() -> dict:
    """Walk the Plex music library and rebuild library.db.

    Returns a dict with track_count, album_count, artist_count, sync_duration_s.
    Raises ValueError if PLEX_URL or PLEX_TOKEN are not configured.
    Raises plexapi exceptions on connection failure — let the caller log/handle.
    """
    if not config.PLEX_URL or not config.PLEX_TOKEN:
        raise ValueError("PLEX_URL and PLEX_TOKEN must be set for Plex library sync")

    from plexapi.server import PlexServer  # noqa: import inside function to avoid hard dep on startup

    start = time.time()
    plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)
    music = plex.library.section(config.PLEX_MUSIC_SECTION)

    _ensure_tables()

    artist_count = 0
    album_count = 0
    track_count = 0

    with _connect() as conn:
        # Walk all artists → albums → tracks
        for plex_artist in music.all():
            artist_id = str(plex_artist.ratingKey)
            artist_name = plex_artist.title or ""

            conn.execute(
                """
                INSERT OR REPLACE INTO artists
                    (id, name, name_lower, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (artist_id, artist_name, artist_name.lower()),
            )
            artist_count += 1

            for plex_album in plex_artist.albums():
                album_id = str(plex_album.ratingKey)
                album_title = plex_album.title or ""
                album_year = getattr(plex_album, "year", None)
                thumb_url = getattr(plex_album, "thumb", None) or ""

                conn.execute(
                    """
                    INSERT OR REPLACE INTO albums
                        (id, artist_id, title, title_lower, year, thumb_url, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (album_id, artist_id, album_title, album_title.lower(),
                     album_year, thumb_url),
                )
                album_count += 1

                for plex_track in plex_album.tracks():
                    track_id = str(plex_track.ratingKey)
                    track_title = plex_track.title or ""
                    track_number = getattr(plex_track, "trackNumber", None)
                    duration = getattr(plex_track, "duration", None)

                    file_path = None
                    if getattr(plex_track, "media", None):
                        media = plex_track.media[0] if plex_track.media else None
                        if media and getattr(media, "parts", None):
                            file_path = getattr(media.parts[0], "file", None)

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO tracks
                            (id, album_id, artist_id, title, title_lower,
                             track_number, duration, file_path, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (track_id, album_id, artist_id, track_title, track_title.lower(),
                         track_number, duration, file_path),
                    )
                    track_count += 1

        duration_s = round(time.time() - start, 1)

        # Write sync stats to library_meta
        meta = {
            "last_synced_ts": str(int(time.time())),
            "track_count": str(track_count),
            "album_count": str(album_count),
            "artist_count": str(artist_count),
            "sync_duration_s": str(duration_s),
        }
        for key, value in meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO library_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    logger.info(
        "plex_reader.sync_library: %d artists, %d albums, %d tracks in %.1fs",
        artist_count, album_count, track_count, duration_s,
    )
    return {
        "track_count": track_count,
        "album_count": album_count,
        "artist_count": artist_count,
        "sync_duration_s": duration_s,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def is_db_accessible() -> bool:
    """Return True if library.db exists and has at least one track row."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
            return row[0] > 0
    except Exception:
        return False


def get_track_count() -> int:
    """Return total track count from library.db."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def get_soulsync_artist_id(artist_name: str) -> str | None:
    """Not applicable for Plex backend."""
    return None


def get_spotify_artist_id(artist_name: str) -> str | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT spotify_artist_id FROM artists WHERE name_lower = lower(?)",
                (artist_name,),
            ).fetchone()
            return row["spotify_artist_id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.get_spotify_artist_id failed: %s", e)
        return None


def get_deezer_artist_id(artist_name: str) -> str | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT deezer_id FROM artists WHERE name_lower = lower(?)",
                (artist_name,),
            ).fetchone()
            return row["deezer_id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.get_deezer_artist_id failed: %s", e)
        return None


def get_itunes_artist_id(artist_name: str) -> str | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT itunes_artist_id FROM artists WHERE name_lower = lower(?)",
                (artist_name,),
            ).fetchone()
            return row["itunes_artist_id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.get_itunes_artist_id failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Owned-check
# ---------------------------------------------------------------------------

def check_album_owned(
    artist_name: str,
    album_name: str,
    soulsync_artist_id: str = None,
    spotify_artist_id: str = None,
    itunes_artist_id: str = None,
    deezer_album_id: str = None,
    spotify_album_id: str = None,
    itunes_album_id: str = None,
) -> str | None:
    """Check whether this album is in library.db. Returns any track ratingKey or None.

    Tier 1a: albums.itunes_album_id exact match (if provided)
    Tier 1b: albums.deezer_id exact match (if provided)
    Tier 1c: albums.spotify_album_id exact match (if provided)
    Tier 0:  artist name_lower + album title_lower text match (primary reliable path)
    """
    try:
        with _connect() as conn:

            def _first_track(album_id: str) -> str | None:
                row = conn.execute(
                    "SELECT id FROM tracks WHERE album_id = ? LIMIT 1",
                    (album_id,),
                ).fetchone()
                return row["id"] if row else None

            # Tier 1a — iTunes album ID
            if itunes_album_id:
                row = conn.execute(
                    "SELECT id FROM albums WHERE itunes_album_id = ?",
                    (itunes_album_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1a hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 1b — Deezer album ID
            if deezer_album_id:
                row = conn.execute(
                    "SELECT id FROM albums WHERE deezer_id = ?",
                    (deezer_album_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1b hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 1c — Spotify album ID
            if spotify_album_id:
                row = conn.execute(
                    "SELECT id FROM albums WHERE spotify_album_id = ?",
                    (spotify_album_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1c hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 0 — artist name + album title text match (primary path for Plex backend)
            row = conn.execute(
                """
                SELECT t.id
                FROM tracks t
                JOIN albums al ON t.album_id = al.id
                JOIN artists ar ON al.artist_id = ar.id
                WHERE ar.name_lower = lower(?)
                  AND al.title_lower = lower(?)
                LIMIT 1
                """,
                (artist_name, album_name),
            ).fetchone()
            if row:
                logger.debug("plex owned-check Tier0 hit: %s / %s", artist_name, album_name)
                return row["id"]

    except Exception as e:
        logger.warning("plex_reader.check_album_owned failed: %s", e)

    return None


def check_owned_exact(spotify_track_id: str) -> str | None:
    """Return track ratingKey if spotify_track_id is in library.db."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE spotify_track_id = ?",
                (spotify_track_id,),
            ).fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.check_owned_exact failed: %s", e)
        return None


def check_owned_deezer(deezer_track_id: str) -> str | None:
    """Return track ratingKey if deezer_id matches in library.db."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE deezer_id = ?",
                (deezer_track_id,),
            ).fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.check_owned_deezer failed: %s", e)
        return None


def find_track_by_name(artist_name: str, track_title: str) -> str | None:
    """Return track ratingKey by artist name + track title text match."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT t.id
                FROM tracks t
                JOIN artists a ON t.artist_id = a.id
                WHERE a.name_lower = lower(?)
                  AND t.title_lower = lower(?)
                LIMIT 1
                """,
                (artist_name, track_title),
            ).fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.find_track_by_name failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Track / Album queries
# ---------------------------------------------------------------------------

def get_all_tracks_for_artist(artist_id: str) -> list[dict]:
    """Return all tracks for an artist. artist_id is the Plex ratingKey.

    Returns list of dicts with keys matching soulsync_reader output:
    plex_rating_key, track_title, track_number, spotify_track_id,
    album_title, album_year, album_thumb_url
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id           AS plex_rating_key,
                    t.title        AS track_title,
                    t.track_number,
                    t.spotify_track_id,
                    al.title       AS album_title,
                    al.year        AS album_year,
                    al.thumb_url   AS album_thumb_url
                FROM tracks t
                JOIN albums al ON t.album_id = al.id
                WHERE al.artist_id = ?
                ORDER BY al.year DESC, t.track_number
                """,
                (artist_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("plex_reader.get_all_tracks_for_artist failed: %s", e)
        return []


def get_tracks_for_album(artist_id: str, album_title: str) -> list[dict]:
    """Return tracks for a specific album. artist_id is the Plex ratingKey."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id           AS plex_rating_key,
                    t.title        AS track_title,
                    t.track_number,
                    t.spotify_track_id,
                    al.title       AS album_title,
                    al.year        AS album_year,
                    al.thumb_url   AS album_thumb_url
                FROM tracks t
                JOIN albums al ON t.album_id = al.id
                WHERE al.artist_id = ?
                  AND al.title_lower = lower(?)
                ORDER BY t.track_number
                """,
                (artist_id, album_title),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("plex_reader.get_tracks_for_album failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Not applicable for Plex backend
# ---------------------------------------------------------------------------

def get_discovery_pool(
    limit: int = 200,
    new_releases_only: bool = False,
    source: str = None,
) -> list[dict]:
    """Not applicable for Plex backend — returns empty list."""
    return []


def get_similar_artists_map(limit: int = 200) -> dict:
    """Not applicable for Plex backend — returns empty dict."""
    return {}
