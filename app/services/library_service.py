"""
library_service.py — ETL orchestrator for the native library engine (Phase 10).

Three-stage pipeline for the Plex backend:
  Stage 1 SYNC    — Walk Plex API → write lib_* tables (delegates to plex_reader)
  Stage 2 ENRICH  — For each lib_album with no itunes_album_id, query iTunes → Deezer
  Stage 3 STATUS  — Return combined sync + enrich progress for the Settings UI

The SoulSync backend does not use this service (it manages its own DB).
The enrich stage is resumable: only processes albums where itunes_album_id IS NULL
AND deezer_id IS NULL, so interrupted runs pick up where they left off.
"""
import logging
import re
import sqlite3
import time
from datetime import datetime
from app import config
from app.db import rythmx_store

logger = logging.getLogger(__name__)

# Enrichment source registry — defines all possible enrichment passes.
# priority: order of execution (lower = first)
# fills: columns populated on lib_albums / lib_artists
# rate_limit_rpm: requests per minute ceiling
# implemented: True = active now; False = registered but deferred
ENRICH_SOURCES = [
    {
        "name": "itunes",
        "priority": 1,
        "fills": ["itunes_album_id", "itunes_artist_id"],
        "rate_limit_rpm": 20,
        "implemented": True,
    },
    {
        "name": "deezer",
        "priority": 2,
        "fills": ["deezer_id"],
        "rate_limit_rpm": 50,
        "implemented": True,
    },
    {
        "name": "musicbrainz",
        "priority": 3,
        "fills": ["musicbrainz_id", "musicbrainz_release_id"],
        "rate_limit_rpm": 1,
        "implemented": False,  # Phase 10
    },
    {
        "name": "spotify",
        "priority": 4,
        "fills": ["spotify_artist_id", "spotify_album_id", "genres_json", "popularity"],
        # NOTE: audio features (energy, valence, etc.) removed Nov 2024 by Spotify for new apps.
        # Columns retained in lib_tracks schema for possible future re-addition.
        "rate_limit_rpm": 100,
        "implemented": True,
    },
    {
        "name": "lastfm_tags",
        "priority": 5,
        "fills": ["lastfm_tags_json"],   # on both lib_artists and lib_albums
        "rate_limit_rpm": 200,
        "implemented": True,
    },
    {
        "name": "deezer_bpm",
        "priority": 6,
        "fills": ["lib_tracks.tempo"],   # BPM from Deezer track endpoint
        "rate_limit_rpm": 50,
        "implemented": True,
    },
]

_ITUNES_BASE = "https://itunes.apple.com"
_ITUNES_RATE_INTERVAL = 3.1  # seconds between calls (20/min limit + margin)
_itunes_last_call: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect():
    """WAL connection to rythmx.db for lib_* read/write."""
    conn = sqlite3.connect(config.RYTHMX_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\(\[](single|ep|deluxe|deluxe\s+edition|explicit|remaster(ed)?|'
    r'expanded|anniversary\s+edition|bonus\s+track[s]?|special\s+edition|'
    r'reissue)[\s\w]*[\)\]]',
    re.IGNORECASE,
)

def _strip_title_suffixes(title: str) -> str:
    """Strip Plex-appended suffixes like [Single], [EP], (Deluxe Edition) before search."""
    return _TITLE_SUFFIX_RE.sub("", title).strip()


def _itunes_search_album(artist_name: str, album_title: str) -> dict | None:
    """
    Query iTunes Search API for a specific album.
    Returns a dict with itunes_album_id and api_title, or None on miss/error.
    Rate-limited to 20 req/min (3.1s between calls).
    """
    global _itunes_last_call
    import requests

    elapsed = time.time() - _itunes_last_call
    if elapsed < _ITUNES_RATE_INTERVAL:
        time.sleep(_ITUNES_RATE_INTERVAL - elapsed)
    _itunes_last_call = time.time()

    try:
        resp = requests.get(
            f"{_ITUNES_BASE}/search",
            params={
                "term": f"{artist_name} {album_title}",
                "media": "music",
                "entity": "album",
                "limit": 5,
                "attribute": "albumTerm",
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        logger.debug("iTunes search failed for '%s / %s': %s", artist_name, album_title, e)
        return None

    if not results:
        return None

    # Find best match: exact artist + album name (case-insensitive)
    artist_lower = artist_name.lower()
    title_lower = album_title.lower()
    for item in results:
        a = (item.get("artistName") or "").lower()
        t = (item.get("collectionName") or "").lower()
        if a == artist_lower and t == title_lower:
            return {
                "itunes_album_id": str(item["collectionId"]),
                "api_title": item.get("collectionName", ""),
                "itunes_artist_id": str(item.get("artistId", "")),
            }

    # Fallback: partial title match (first result where artist matches)
    for item in results:
        a = (item.get("artistName") or "").lower()
        t = (item.get("collectionName") or "").lower()
        if a == artist_lower and title_lower in t:
            return {
                "itunes_album_id": str(item["collectionId"]),
                "api_title": item.get("collectionName", ""),
                "itunes_artist_id": str(item.get("artistId", "")),
            }

    return None


def _deezer_search_album(artist_name: str, album_title: str) -> dict | None:
    """
    Query Deezer Search API for a specific album.
    Returns a dict with deezer_id and api_title, or None on miss/error.
    No auth required. Free tier, no enforced rate limit.
    """
    import requests

    try:
        resp = requests.get(
            "https://api.deezer.com/search/album",
            params={"q": f'artist:"{artist_name}" album:"{album_title}"', "limit": 5},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
    except Exception as e:
        logger.debug("Deezer search failed for '%s / %s': %s", artist_name, album_title, e)
        return None

    if not items:
        return None

    artist_lower = artist_name.lower()
    title_lower = album_title.lower()
    for item in items:
        a = (item.get("artist", {}).get("name") or "").lower()
        t = (item.get("title") or "").lower()
        if a == artist_lower and t == title_lower:
            return {"deezer_id": str(item["id"]), "api_title": item.get("title", "")}

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_library() -> dict:
    """
    Stage 1: Walk the active library backend → write lib_* tables.
    Routes to the correct backend (plex_reader or soulsync_reader) via get_library_reader().
    After sync, prunes lib_releases rows older than 180 days with is_owned=0.
    Returns {artist_count, album_count, track_count, sync_duration_s}.
    """
    from app.db import get_library_reader
    result = get_library_reader().sync_library()
    _prune_old_releases()
    return result


def _prune_old_releases() -> None:
    """Delete lib_releases rows older than 180 days that are not owned.
    Owned releases are kept indefinitely. Called after each library sync.
    """
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM lib_releases "
                "WHERE is_owned = 0 "
                "AND first_seen_at < datetime('now', '-180 days')"
            )
    except Exception as e:
        logger.warning("prune_old_releases failed (table may not exist yet): %s", e)


def _write_enrichment_meta(conn, source: str, entity_type: str, entity_id: str,
                           status: str, error_msg: str | None = None) -> None:
    """Upsert a row into enrichment_meta. Silently ignores if table doesn't exist yet."""
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO enrichment_meta
                (source, entity_type, entity_id, status, enriched_at, error_msg)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """,
            (source, entity_type, entity_id, status, error_msg),
        )
    except Exception as e:
        logger.debug("enrichment_meta write skipped: %s", e)


def enrich_library(batch_size: int = 50) -> dict:
    """
    Stage 2: For each lib_album missing iTunes + Deezer IDs, query iTunes then Deezer.
    Resumable: skips albums already marked not_found in enrichment_meta.
    Micro-batched: fetches batch_size albums, commits after each batch.
    Returns {enriched, failed, skipped, remaining}.
    """
    enriched = 0
    failed = 0
    skipped = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT la.id, la.title, la.artist_id, la.local_title,
                       ar.name AS artist_name
                FROM lib_albums la
                JOIN lib_artists ar ON la.artist_id = ar.id
                WHERE la.itunes_album_id IS NULL
                  AND la.deezer_id IS NULL
                  AND la.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album'
                        AND status = 'not_found'
                        AND source IN ('itunes', 'deezer')
                      GROUP BY entity_id
                      HAVING COUNT(DISTINCT source) >= 2
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_library: could not read lib_albums: %s", e)
        return {"enriched": 0, "failed": 0, "skipped": 0, "remaining": -1, "error": str(e)}

    if not rows:
        logger.info("enrich_library: nothing to enrich — all albums have IDs")
        return {"enriched": 0, "failed": 0, "skipped": 0, "remaining": 0}

    for album in rows:
        album_id = album["id"]
        artist_name = album["artist_name"]
        album_title = _strip_title_suffixes(album["local_title"] or album["title"])

        itunes_result = _itunes_search_album(artist_name, album_title)
        if itunes_result:
            try:
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_albums
                        SET itunes_album_id = ?,
                            api_title = ?,
                            match_confidence = 90,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (itunes_result["itunes_album_id"],
                         itunes_result.get("api_title", ""),
                         album_id),
                    )
                    # Back-fill itunes_artist_id on lib_artists if not set
                    if itunes_result.get("itunes_artist_id"):
                        conn.execute(
                            """
                            UPDATE lib_artists
                            SET itunes_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND itunes_artist_id IS NULL
                            """,
                            (itunes_result["itunes_artist_id"], album["artist_id"]),
                        )
                    _write_enrichment_meta(conn, "itunes", "album", album_id, "found")
                enriched += 1
                logger.debug(
                    "Enrich: iTunes hit for '%s / %s' → id=%s",
                    artist_name, album_title, itunes_result["itunes_album_id"],
                )
                continue
            except Exception as e:
                logger.warning("Enrich: DB write failed for '%s / %s': %s",
                               artist_name, album_title, e)
                failed += 1
                continue

        # iTunes miss — record it so we don't hammer the API again
        try:
            with _connect() as conn:
                _write_enrichment_meta(conn, "itunes", "album", album_id, "not_found")
        except Exception:
            pass

        # iTunes miss → try Deezer
        deezer_result = _deezer_search_album(artist_name, album_title)
        if deezer_result:
            try:
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_albums
                        SET deezer_id = ?,
                            api_title = ?,
                            match_confidence = 75,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (deezer_result["deezer_id"],
                         deezer_result.get("api_title", ""),
                         album_id),
                    )
                    _write_enrichment_meta(conn, "deezer", "album", album_id, "found")
                enriched += 1
                logger.debug(
                    "Enrich: Deezer hit for '%s / %s' → id=%s",
                    artist_name, album_title, deezer_result["deezer_id"],
                )
                continue
            except Exception as e:
                logger.warning("Enrich: DB write failed for '%s / %s': %s",
                               artist_name, album_title, e)
                failed += 1
                continue

        # Both misses — record not_found for Deezer; album excluded from future runs
        try:
            with _connect() as conn:
                conn.execute(
                    """
                    UPDATE lib_albums
                    SET match_confidence = 0,
                        needs_verification = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (album_id,),
                )
                _write_enrichment_meta(conn, "deezer", "album", album_id, "not_found")
        except Exception:
            pass
        skipped += 1
        logger.debug("Enrich: no match for '%s / %s'", artist_name, album_title)

    # Count remaining unenriched albums
    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE itunes_album_id IS NULL AND deezer_id IS NULL"
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info(
        "enrich_library: enriched=%d, skipped=%d, failed=%d, remaining=%d",
        enriched, skipped, failed, remaining,
    )
    return {"enriched": enriched, "failed": failed, "skipped": skipped, "remaining": remaining}


def enrich_spotify(batch_size: int = 20) -> dict:
    """
    Spotify enrichment pass: for each lib_artist missing spotify_artist_id,
    resolve via Spotify API → store raw JSON → extract genres, popularity.
    Also fetches appears_on albums and audio features for owned tracks.

    Writes raw API responses to spotify_raw_cache for dev replay / API expiry survival.
    Rate-limited via config.SPOTIFY_RATE_LIMIT_RPM.
    Resumable: skips artists already in enrichment_meta with source='spotify'.
    Returns {enriched, skipped, failed, remaining}.
    """
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1,
                "error": "Spotify credentials not configured"}

    try:
        import spotipy  # type: ignore[import]
        from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore[import]
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
    except Exception as e:
        logger.error("enrich_spotify: Spotify client init failed: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name FROM lib_artists
                WHERE spotify_artist_id IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify'
                        AND status IN ('found', 'not_found')
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_spotify: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        logger.info("enrich_spotify: nothing to enrich — all artists have Spotify IDs")
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    import json

    for artist in rows:
        artist_id = artist["id"]
        artist_name = artist["name"]

        try:
            # --- Search for artist ---
            from app.clients.music_client import _spotify_rate_limit, norm
            _spotify_rate_limit()
            results = sp.search(q=f'artist:"{artist_name}"', type="artist", limit=5)
            items = results.get("artists", {}).get("items", [])
            if not items:
                logger.debug("enrich_spotify: no Spotify match for '%s'", artist_name)
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify", "artist", artist_id, "not_found")
                skipped += 1
                continue

            norm_name = norm(artist_name)
            match = next((a for a in items if norm(a["name"]) == norm_name), items[0])
            spotify_artist_id = match["id"]

            # --- Fetch full artist object (genres, popularity, images) ---
            _spotify_rate_limit()
            artist_data = sp.artist(spotify_artist_id)

            # --- Fetch appears_on albums ---
            _spotify_rate_limit()
            appears_on_data = sp.artist_albums(
                spotify_artist_id, include_groups="appears_on", limit=20
            )

            # --- Write raw cache ---
            with _connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO spotify_raw_cache
                        (query_type, entity_id, entity_name, raw_json, fetched_at)
                    VALUES ('artist', ?, ?, ?, datetime('now'))
                    """,
                    (spotify_artist_id, artist_name, json.dumps(artist_data)),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO spotify_raw_cache
                        (query_type, entity_id, entity_name, raw_json, fetched_at)
                    VALUES ('appears_on', ?, ?, ?, datetime('now'))
                    """,
                    (spotify_artist_id, artist_name, json.dumps(appears_on_data)),
                )

                # --- Extract + write columns ---
                genres_json = json.dumps(artist_data.get("genres", []))
                popularity = artist_data.get("popularity")
                conn.execute(
                    """
                    UPDATE lib_artists
                    SET spotify_artist_id = ?,
                        genres_json = ?,
                        popularity = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (spotify_artist_id, genres_json, popularity, artist_id),
                )
                _write_enrichment_meta(conn, "spotify", "artist", artist_id, "found")

            enriched += 1
            logger.debug(
                "enrich_spotify: hit for '%s' → id=%s genres=%s popularity=%s",
                artist_name, spotify_artist_id,
                artist_data.get("genres", [])[:3], popularity,
            )

        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("enrich_spotify: rate limit hit on '%s' — stopping batch", artist_name)
                break
            logger.warning("enrich_spotify: failed for '%s': %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify", "artist", artist_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1

    # Count remaining
    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE spotify_artist_id IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify'
                        AND status IN ('found', 'not_found')
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_spotify: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def get_spotify_status() -> dict:
    """Return Spotify enrichment status for the Settings UI."""
    try:
        with _connect() as conn:
            total_row = conn.execute("SELECT COUNT(*) FROM lib_artists").fetchone()
            total = total_row[0] if total_row else 0

            enriched_row = conn.execute(
                "SELECT COUNT(*) FROM lib_artists WHERE spotify_artist_id IS NOT NULL"
            ).fetchone()
            enriched = enriched_row[0] if enriched_row else 0
    except Exception:
        total = 0
        enriched = 0

    last_run = rythmx_store.get_setting("spotify_enrich_last_run")
    return {
        "enriched_artists": enriched,
        "total_artists": total,
        "last_run": last_run,
        "spotify_available": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
    }


def enrich_lastfm_tags(batch_size: int = 50) -> dict:
    """
    Last.fm genre tag enrichment pass.

    Artist pass: fetches artist.getTopTags for each lib_artist missing lastfm_tags_json.
    Album pass: fetches album.getTopTags for each lib_album missing lastfm_tags_json;
                falls back to parent artist's tags when Last.fm has no album-level data.

    Resumable — skips rows already in enrichment_meta(source='lastfm_tags').
    Returns {enriched_artists, enriched_albums, skipped, failed, remaining_artists, remaining_albums}.
    """
    from app.clients.last_fm_client import get_artist_tags, get_album_tags
    import json

    enriched_artists = 0
    enriched_albums = 0
    skipped = 0
    failed = 0

    # --- Artist pass ---
    try:
        with _connect() as conn:
            artist_rows = conn.execute(
                """
                SELECT id, name FROM lib_artists
                WHERE lastfm_tags_json IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_tags'
                        AND status IN ('found', 'not_found')
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_lastfm_tags: could not read lib_artists: %s", e)
        return {"enriched_artists": 0, "enriched_albums": 0, "skipped": 0,
                "failed": 0, "remaining_artists": -1, "remaining_albums": -1, "error": str(e)}

    for artist in artist_rows:
        artist_id, artist_name = artist["id"], artist["name"]
        try:
            tags = get_artist_tags(artist_name)
            tags_json = json.dumps(tags)
            status = "found" if tags else "not_found"
            with _connect() as conn:
                conn.execute(
                    "UPDATE lib_artists SET lastfm_tags_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (tags_json, artist_id),
                )
                _write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, status)
            enriched_artists += 1
            logger.debug("enrich_lastfm_tags artist '%s': %s", artist_name, tags)
        except Exception as e:
            logger.warning("enrich_lastfm_tags: artist '%s' failed: %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1

    # --- Album pass ---
    try:
        with _connect() as conn:
            album_rows = conn.execute(
                """
                SELECT a.id, a.title, a.artist_id,
                       ar.name AS artist_name, ar.lastfm_tags_json AS artist_tags
                FROM lib_albums a
                JOIN lib_artists ar ON ar.id = a.artist_id
                WHERE a.lastfm_tags_json IS NULL
                  AND a.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'lastfm_tags'
                        AND status IN ('found', 'not_found', 'fallback')
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_lastfm_tags: could not read lib_albums: %s", e)
        album_rows = []

    for album in album_rows:
        album_id = album["id"]
        album_title = album["title"]
        artist_name = album["artist_name"]
        artist_tags_json = album["artist_tags"]
        try:
            tags = get_album_tags(artist_name, album_title)
            if tags:
                tags_json = json.dumps(tags)
                status = "found"
            else:
                # Fallback: use parent artist's tags
                tags_json = artist_tags_json or json.dumps([])
                status = "fallback"
            with _connect() as conn:
                conn.execute(
                    "UPDATE lib_albums SET lastfm_tags_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (tags_json, album_id),
                )
                _write_enrichment_meta(conn, "lastfm_tags", "album", album_id, status)
            enriched_albums += 1
            logger.debug("enrich_lastfm_tags album '%s / %s': %s (status=%s)",
                         artist_name, album_title, tags, status)
        except Exception as e:
            logger.warning("enrich_lastfm_tags: album '%s / %s' failed: %s",
                           artist_name, album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_tags", "album", album_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1

    # Count remaining
    try:
        with _connect() as conn:
            rem_artists = conn.execute(
                """SELECT COUNT(*) FROM lib_artists WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='artist' AND source='lastfm_tags'
                                    AND status IN ('found','not_found'))"""
            ).fetchone()[0]
            rem_albums = conn.execute(
                """SELECT COUNT(*) FROM lib_albums WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='album' AND source='lastfm_tags'
                                    AND status IN ('found','not_found','fallback'))"""
            ).fetchone()[0]
    except Exception:
        rem_artists = rem_albums = -1

    logger.info("enrich_lastfm_tags: artists=%d albums=%d failed=%d remaining=%d/%d",
                enriched_artists, enriched_albums, failed, rem_artists, rem_albums)
    return {
        "enriched_artists": enriched_artists,
        "enriched_albums": enriched_albums,
        "skipped": skipped,
        "failed": failed,
        "remaining_artists": rem_artists,
        "remaining_albums": rem_albums,
    }


def get_lastfm_tags_status() -> dict:
    """Return Last.fm tag enrichment status for the Settings UI."""
    try:
        with _connect() as conn:
            total_artists = conn.execute("SELECT COUNT(*) FROM lib_artists").fetchone()[0]
            enriched_artists = conn.execute(
                "SELECT COUNT(*) FROM lib_artists WHERE lastfm_tags_json IS NOT NULL"
            ).fetchone()[0]
            total_albums = conn.execute("SELECT COUNT(*) FROM lib_albums").fetchone()[0]
            enriched_albums = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE lastfm_tags_json IS NOT NULL"
            ).fetchone()[0]
    except Exception:
        total_artists = enriched_artists = total_albums = enriched_albums = 0

    last_run = rythmx_store.get_setting("lastfm_tags_last_run")
    return {
        "enriched_artists": enriched_artists,
        "total_artists": total_artists,
        "enriched_albums": enriched_albums,
        "total_albums": total_albums,
        "last_run": last_run,
        "lastfm_available": bool(config.LASTFM_API_KEY),
    }


def get_status() -> dict:
    """
    Return combined sync + enrich status for the Settings UI.
    Always safe to call — returns sane defaults if tables don't exist yet.
    """
    last_synced = rythmx_store.get_setting("library_last_synced")
    backend = rythmx_store.get_setting("library_backend") or config.LIBRARY_BACKEND

    try:
        with _connect() as conn:
            track_row = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()
            track_count = track_row[0] if track_row else 0

            album_row = conn.execute("SELECT COUNT(*) FROM lib_albums").fetchone()
            total_albums = album_row[0] if album_row else 0

            enriched_row = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE itunes_album_id IS NOT NULL OR deezer_id IS NOT NULL"
            ).fetchone()
            enriched_albums = enriched_row[0] if enriched_row else 0
    except Exception:
        track_count = 0
        total_albums = 0
        enriched_albums = 0

    enrich_pct = round(enriched_albums / total_albums * 100) if total_albums else 0

    return {
        "synced": track_count > 0,
        "last_synced": last_synced,
        "backend": backend,
        "track_count": track_count,
        "total_albums": total_albums,
        "enriched_albums": enriched_albums,
        "enrich_pct": enrich_pct,
    }


# ---------------------------------------------------------------------------
# Deezer BPM enrichment
# ---------------------------------------------------------------------------

_DEEZER_ALBUM_URL = "https://api.deezer.com/album/{album_id}/tracks"
_DEEZER_TRACK_URL = "https://api.deezer.com/track/{track_id}"
_DEEZER_BPM_RATE_INTERVAL = 1.2   # seconds between calls (~50/min, conservative)
_deezer_bpm_last_call: float = 0.0


def _deezer_rate_limited_get(url: str) -> dict | None:
    """Single rate-limited GET to Deezer. Returns parsed JSON or None on error."""
    global _deezer_bpm_last_call
    import requests

    elapsed = time.time() - _deezer_bpm_last_call
    if elapsed < _DEEZER_BPM_RATE_INTERVAL:
        time.sleep(_DEEZER_BPM_RATE_INTERVAL - elapsed)
    _deezer_bpm_last_call = time.time()

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug("Deezer request failed for %s: %s", url, e)
        return None


def _fetch_deezer_album_tracks(deezer_album_id: str) -> list[dict]:
    """
    Fetch BPM for all tracks in a Deezer album.

    Two-pass: first GET /album/{id}/tracks for track IDs, then GET /track/{id}
    per track for BPM (bpm is not included in the album tracks list response).

    Returns list of {title, bpm} dicts. Empty on error or no tracks.
    """
    # Pass 1: get track IDs from album endpoint
    data = _deezer_rate_limited_get(_DEEZER_ALBUM_URL.format(album_id=deezer_album_id))
    if not data:
        return []
    track_stubs = data.get("data", [])
    if not track_stubs:
        return []

    # Pass 2: fetch each track individually to get BPM
    results = []
    for stub in track_stubs:
        track_id = stub.get("id")
        title = stub.get("title", "")
        if not track_id:
            continue
        track_data = _deezer_rate_limited_get(_DEEZER_TRACK_URL.format(track_id=track_id))
        if not track_data:
            continue
        bpm = float(track_data.get("bpm", 0) or 0)
        if bpm > 0:
            results.append({"title": title, "bpm": bpm})

    return results


def enrich_deezer_bpm(batch_size: int = 30) -> dict:
    """
    Deezer BPM enrichment pass.

    For each lib_album with a deezer_id, fetch the Deezer track list and write
    bpm → lib_tracks.tempo using exact title match (title_lower).

    Only processes albums not already in enrichment_meta(source='deezer_bpm').
    Resumable — interrupted runs pick up where they left off.

    Returns {enriched_tracks, enriched_albums, failed, skipped, remaining}.
    """
    import json

    enriched_tracks = 0
    enriched_albums = 0
    failed = 0
    skipped = 0

    # Load albums that have a deezer_id but haven't been BPM-enriched yet
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT la.id, la.deezer_id, ar.name AS artist_name, la.title
                FROM lib_albums la
                JOIN lib_artists ar ON la.artist_id = ar.id
                WHERE la.deezer_id IS NOT NULL
                  AND la.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'deezer_bpm'
                        AND status IN ('found', 'not_found')
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_deezer_bpm: could not read lib_albums: %s", e)
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": -1, "error": str(e)}

    if not rows:
        logger.info("enrich_deezer_bpm: nothing to enrich")
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": 0}

    for album in rows:
        album_id = album["id"]
        deezer_album_id = album["deezer_id"]
        artist_name = album["artist_name"]
        album_title = album["title"]

        deezer_tracks = _fetch_deezer_album_tracks(deezer_album_id)

        if not deezer_tracks:
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_bpm", "album", album_id, "not_found")
                skipped += 1
            except Exception:
                pass
            continue

        # Build lookup: title_lower → bpm
        bpm_map = {t["title"].lower(): t["bpm"] for t in deezer_tracks}

        try:
            with _connect() as conn:
                # Match lib_tracks for this album by title_lower
                lib_tracks = conn.execute(
                    "SELECT id, title_lower FROM lib_tracks WHERE album_id = ?",
                    (album_id,),
                ).fetchall()

                updated = 0
                for track in lib_tracks:
                    bpm = bpm_map.get(track["title_lower"])
                    if bpm:
                        conn.execute(
                            "UPDATE lib_tracks SET tempo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (bpm, track["id"]),
                        )
                        updated += 1

                _write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                       "found" if updated > 0 else "not_found")

            enriched_tracks += updated
            enriched_albums += 1
            logger.debug(
                "enrich_deezer_bpm: '%s / %s' → %d tracks updated",
                artist_name, album_title, updated,
            )
        except Exception as e:
            logger.warning("enrich_deezer_bpm: failed for '%s / %s': %s",
                           artist_name, album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                           "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1

    logger.info(
        "enrich_deezer_bpm: enriched_tracks=%d enriched_albums=%d failed=%d skipped=%d",
        enriched_tracks, enriched_albums, failed, skipped,
    )
    return {
        "enriched_tracks": enriched_tracks,
        "enriched_albums": enriched_albums,
        "failed": failed,
        "skipped": skipped,
        "remaining": len(rows),
    }


def get_deezer_bpm_status() -> dict:
    """
    Returns {enriched_albums, total_albums_with_deezer, enriched_tracks,
             total_tracks, last_run}.
    total_albums_with_deezer is the pool that can be enriched.
    """
    try:
        with _connect() as conn:
            total_albums = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE deezer_id IS NOT NULL"
            ).fetchone()[0]
            enriched_albums = conn.execute(
                """
                SELECT COUNT(*) FROM enrichment_meta
                WHERE source = 'deezer_bpm' AND entity_type = 'album'
                  AND status = 'found'
                """
            ).fetchone()[0]
            enriched_tracks = conn.execute(
                "SELECT COUNT(*) FROM lib_tracks WHERE tempo IS NOT NULL AND tempo > 0"
            ).fetchone()[0]
            total_tracks = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()[0]
    except Exception:
        total_albums = enriched_albums = enriched_tracks = total_tracks = 0

    last_run = rythmx_store.get_setting("deezer_bpm_last_run")
    return {
        "enriched_albums": enriched_albums,
        "total_albums": total_albums,
        "enriched_tracks": enriched_tracks,
        "total_tracks": total_tracks,
        "last_run": last_run,
    }
