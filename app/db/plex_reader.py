"""
plex_reader.py — Plex-native library backend.

Implements the library reader interface (see soulsync_reader.py for the contract).
Reads from lib_* tables in rythmx.db, built and maintained by sync_library().

sync_library() walks the Plex music library via python-plexapi and captures
ratingKeys directly — the same mechanism SoulSync uses. All other functions
query rythmx.db only; no network calls outside of sync_library().

The lib_* tables are a derived cache — safe to delete and re-sync at any time.
Tables are created by migrations/002_add_lib_tables.sql (not here).

Functions not applicable to this backend return safe empty values:
  get_discovery_pool()       → []    (SoulSync-specific)
  get_similar_artists_map()  → {}    (SoulSync-specific)
  get_soulsync_artist_id()   → None  (not applicable)
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
    """Return a WAL-mode connection to rythmx.db (read/write, rythmx-owned)."""
    conn = sqlite3.connect(config.RYTHMX_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_library() -> dict:
    """Walk the Plex music library and rebuild lib_* tables in rythmx.db.

    Micro-batched: commits every 50 artists to keep WAL size manageable.
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

    artist_count = 0
    album_count = 0
    track_count = 0

    with _connect() as conn:
        all_artists = list(music.all())

        for i, plex_artist in enumerate(all_artists):
            artist_id = str(plex_artist.ratingKey)
            artist_name = plex_artist.title or ""

            conn.execute(
                """
                INSERT OR REPLACE INTO lib_artists
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
                    INSERT OR REPLACE INTO lib_albums
                        (id, artist_id, title, local_title, title_lower, year, thumb_url, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (album_id, artist_id, album_title, album_title,
                     album_title.lower(), album_year, thumb_url),
                )
                album_count += 1

                for plex_track in plex_album.tracks():
                    track_id = str(plex_track.ratingKey)
                    track_title = plex_track.title or ""
                    track_number = getattr(plex_track, "trackNumber", None)
                    duration = getattr(plex_track, "duration", None)

                    file_path = None
                    file_size = None
                    if getattr(plex_track, "media", None):
                        media = plex_track.media[0] if plex_track.media else None
                        if media and getattr(media, "parts", None):
                            part = media.parts[0]
                            file_path = getattr(part, "file", None)
                            file_size = getattr(part, "size", None)

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO lib_tracks
                            (id, album_id, artist_id, title, title_lower,
                             track_number, duration, file_path, file_size, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (track_id, album_id, artist_id, track_title, track_title.lower(),
                         track_number, duration, file_path, file_size),
                    )
                    track_count += 1

            # Micro-batch commit every 50 artists to keep WAL manageable
            if (i + 1) % 50 == 0:
                conn.commit()
                logger.debug("sync_library: committed batch at artist %d", i + 1)

        duration_s = round(time.time() - start, 1)

        # Write sync stats to lib_meta
        meta = {
            "last_synced_ts": str(int(time.time())),
            "track_count": str(track_count),
            "album_count": str(album_count),
            "artist_count": str(artist_count),
            "sync_duration_s": str(duration_s),
        }
        for key, value in meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO lib_meta (key, value) VALUES (?, ?)",
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
    """Return True if lib_tracks exists and has at least one row."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()
            return row[0] > 0
    except Exception:
        return False


def get_track_count() -> int:
    """Return total track count from lib_tracks."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()
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
                "SELECT spotify_artist_id FROM lib_artists WHERE name_lower = lower(?)",
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
                "SELECT deezer_id FROM lib_artists WHERE name_lower = lower(?)",
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
                "SELECT itunes_artist_id FROM lib_artists WHERE name_lower = lower(?)",
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
    musicbrainz_release_id: str = None,
) -> str | None:
    """Check whether this album is in lib_* tables. Returns any track ratingKey or None.

    Tier 1a: lib_albums.itunes_album_id exact match (filled by Enrich stage)
    Tier 1b: lib_albums.deezer_id exact match (filled by Enrich stage)
    Tier 1c: lib_albums.spotify_album_id exact match
    Tier 1d: lib_albums.musicbrainz_release_id exact match
    Tier 0:  artist name_lower + album title_lower text match (always available)
    """
    try:
        with _connect() as conn:

            def _first_track(album_id: str) -> str | None:
                row = conn.execute(
                    "SELECT id FROM lib_tracks WHERE album_id = ? LIMIT 1",
                    (album_id,),
                ).fetchone()
                return row["id"] if row else None

            # Tier 1a — iTunes album ID (filled by Enrich stage)
            if itunes_album_id:
                row = conn.execute(
                    "SELECT id FROM lib_albums WHERE itunes_album_id = ?",
                    (itunes_album_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1a hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 1b — Deezer album ID (filled by Enrich stage)
            if deezer_album_id:
                row = conn.execute(
                    "SELECT id FROM lib_albums WHERE deezer_id = ?",
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
                    "SELECT id FROM lib_albums WHERE spotify_album_id = ?",
                    (spotify_album_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1c hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 1d — MusicBrainz release ID
            if musicbrainz_release_id:
                row = conn.execute(
                    "SELECT id FROM lib_albums WHERE musicbrainz_release_id = ?",
                    (musicbrainz_release_id,),
                ).fetchone()
                if row:
                    result = _first_track(row["id"])
                    if result:
                        logger.debug("plex owned-check Tier1d hit: %s / %s", artist_name, album_name)
                        return result

            # Tier 0 — artist name + album title text match (always available after sync)
            row = conn.execute(
                """
                SELECT t.id
                FROM lib_tracks t
                JOIN lib_albums al ON t.album_id = al.id
                JOIN lib_artists ar ON al.artist_id = ar.id
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
    """Return track ratingKey if spotify_track_id is in lib_tracks."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM lib_tracks WHERE spotify_track_id = ?",
                (spotify_track_id,),
            ).fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.debug("plex_reader.check_owned_exact failed: %s", e)
        return None


def check_owned_deezer(deezer_track_id: str) -> str | None:
    """Return track ratingKey if deezer_id matches in lib_tracks."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM lib_tracks WHERE deezer_id = ?",
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
                FROM lib_tracks t
                JOIN lib_artists a ON t.artist_id = a.id
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
                FROM lib_tracks t
                JOIN lib_albums al ON t.album_id = al.id
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
                FROM lib_tracks t
                JOIN lib_albums al ON t.album_id = al.id
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
