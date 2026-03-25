"""
tags_lastfm.py — Stage 3 Last.fm tags worker (artist + album) + wrapper + status.

Dual-pass design: artist tags first, then album tags with fallback to artist tags.
Normalizes via LASTFM_GENRE_WHITELIST (top 5 canonical labels).
"""
import json
import logging
import threading

from app import config
from app.db import rythmx_store
from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.enrichment._helpers import normalize_lastfm_tags

logger = logging.getLogger(__name__)


def enrich_tags_lastfm(batch_size: int = 50, stop_event: threading.Event | None = None,
                        on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Last.fm tags worker (artist + album).
    Artists without lastfm_mbid are still attempted by name (graceful degradation).
    Normalizes tags inline: top 5, whitelist-filtered, canonical labels.
    """
    from app.clients.last_fm_client import get_artist_tags, get_album_tags

    enriched_artists = 0
    enriched_albums = 0
    skipped = 0
    failed = 0

    # --- Artist pass ---
    try:
        with _connect() as conn:
            artist_rows = conn.execute(
                """
                SELECT id, name, lastfm_mbid FROM lib_artists
                WHERE lastfm_tags_json IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_tags'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_tags_lastfm: could not read lib_artists: %s", e)
        return {"enriched_artists": 0, "enriched_albums": 0, "skipped": 0,
                "failed": 0, "remaining_artists": -1, "remaining_albums": -1, "error": str(e)}

    for artist in artist_rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]

        try:
            conn = _connect()
        except Exception:
            failed += 1
            continue

        try:
            raw_tags = get_artist_tags(artist_name)
            canonical = normalize_lastfm_tags(raw_tags)
            tags_json = json.dumps(canonical)
            status = "found" if canonical else "not_found"
            conn.execute(
                "UPDATE lib_artists SET lastfm_tags_json = COALESCE(?, lastfm_tags_json), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (tags_json, artist_id),
            )
            write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, status)
            enriched_artists += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows))
            logger.debug("enrich_tags_lastfm artist '%s': %s", artist_name, canonical)
        except Exception as e:
            logger.warning("enrich_tags_lastfm: artist '%s' failed: %s", artist_name, e)
            write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, "error",
                                  error_msg=str(e)[:200])
            failed += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows))
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

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
                        AND (status = 'found'
                             OR status = 'fallback'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_tags_lastfm: could not read lib_albums: %s", e)
        album_rows = []

    for album in album_rows:
        album_id = album["id"]
        album_title = album["title"]
        artist_name = album["artist_name"]
        artist_tags_json = album["artist_tags"]

        try:
            conn = _connect()
        except Exception:
            failed += 1
            continue

        try:
            raw_tags = get_album_tags(artist_name, album_title)
            if raw_tags:
                canonical = normalize_lastfm_tags(raw_tags)
                tags_json = json.dumps(canonical)
                status = "found"
            else:
                tags_json = artist_tags_json or json.dumps([])
                status = "fallback"
            conn.execute(
                "UPDATE lib_albums SET lastfm_tags_json = COALESCE(?, lastfm_tags_json), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (tags_json, album_id),
            )
            write_enrichment_meta(conn, "lastfm_tags", "album", album_id, status)
            enriched_albums += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed,
                            len(artist_rows) + len(album_rows))
            logger.debug("enrich_tags_lastfm album '%s / %s': status=%s",
                         artist_name, album_title, status)
        except Exception as e:
            logger.warning("enrich_tags_lastfm: album '%s / %s' failed: %s",
                           artist_name, album_title, e)
            write_enrichment_meta(conn, "lastfm_tags", "album", album_id, "error",
                                  error_msg=str(e)[:200])
            failed += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed,
                            len(artist_rows) + len(album_rows))
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

    # Count remaining
    try:
        with _connect() as conn:
            rem_artists = conn.execute(
                """SELECT COUNT(*) FROM lib_artists WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='artist' AND source='lastfm_tags'
                                    AND (status = 'found'
                                         OR (status = 'not_found'
                                             AND (retry_after IS NULL
                                                  OR retry_after > date('now')))))"""
            ).fetchone()[0]
            rem_albums = conn.execute(
                """SELECT COUNT(*) FROM lib_albums WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='album' AND source='lastfm_tags'
                                    AND (status IN ('found', 'fallback')
                                         OR (status = 'not_found'
                                             AND (retry_after IS NULL
                                                  OR retry_after > date('now')))))"""
            ).fetchone()[0]
    except Exception:
        rem_artists = rem_albums = -1

    logger.info("enrich_tags_lastfm: artists=%d albums=%d failed=%d remaining=%d/%d",
                enriched_artists, enriched_albums, failed, rem_artists, rem_albums)
    return {
        "enriched_artists": enriched_artists,
        "enriched_albums": enriched_albums,
        "skipped": skipped,
        "failed": failed,
        "remaining_artists": rem_artists,
        "remaining_albums": rem_albums,
    }


def enrich_lastfm_tags(batch_size: int = 50) -> dict:
    """Thin wrapper — runs Stage 2 (MBID resolution) then Stage 3 (tags)."""
    from app.services.enrichment.id_lastfm import enrich_artist_ids_lastfm
    s2 = enrich_artist_ids_lastfm(batch_size)
    s3 = enrich_tags_lastfm(batch_size)
    return {
        "enriched_artists": s3.get("enriched_artists", 0),
        "enriched_albums": s3.get("enriched_albums", 0),
        "skipped": s2.get("skipped", 0) + s3.get("skipped", 0),
        "failed": s2.get("failed", 0) + s3.get("failed", 0),
        "remaining_artists": s3.get("remaining_artists", -1),
        "remaining_albums": s3.get("remaining_albums", -1),
        "stage2": s2,
        "stage3": s3,
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
