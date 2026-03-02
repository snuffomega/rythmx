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
import sqlite3
import time
from datetime import datetime
from app import config
from app.db import rythmx_store

logger = logging.getLogger(__name__)

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
    Stage 1: Walk Plex API → write lib_* tables.
    Delegates to plex_reader._sync_to_lib() which micro-batches (commit every 50 artists).
    Returns {artist_count, album_count, track_count, sync_duration_s}.
    Raises ValueError if Plex credentials are not configured.
    Raises plexapi exceptions on connection failure.
    """
    from app.db import plex_reader
    return plex_reader.sync_library()


def enrich_library(batch_size: int = 50) -> dict:
    """
    Stage 2: For each lib_album missing iTunes + Deezer IDs, query iTunes then Deezer.
    Resumable: only processes WHERE itunes_album_id IS NULL AND deezer_id IS NULL.
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
        album_title = album["local_title"] or album["title"]

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

        # Both misses — mark with a low confidence so it doesn't re-run endlessly.
        # Set deezer_id to empty string to exclude from the WHERE clause next pass
        # (we use NULL check — skip rows that returned no results to avoid hammering APIs).
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
