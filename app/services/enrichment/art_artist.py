"""
art_artist.py - Artist photo enrichment worker.

Resolution order:
  1. Fanart.tv (primary upgrade path)
  2. Deezer artist photo (fallback)

Stores local artwork in image_cache (content_hash/local_path/artwork_source)
and keeps per-source URL columns on lib_artists for compatibility.
"""
from __future__ import annotations

import logging

import requests

from app import config
from app.services.artwork_store import get_original_path, ingest
from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT a.id,
           a.name,
           COALESCE(a.musicbrainz_id, a.lastfm_mbid) AS artist_mbid,
           a.deezer_artist_id,
           ic.content_hash,
           ic.artwork_source
    FROM lib_artists a
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'artist' AND ic.entity_key = a.id
    WHERE a.removed_at IS NULL
      AND (a.deezer_artist_id IS NOT NULL OR COALESCE(a.musicbrainz_id, a.lastfm_mbid) IS NOT NULL)
      AND (ic.content_hash IS NULL OR ic.artwork_source = 'deezer')
"""

_REMAINING_SQL = """
    SELECT COUNT(*)
    FROM lib_artists a
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'artist' AND ic.entity_key = a.id
    WHERE a.removed_at IS NULL
      AND (a.deezer_artist_id IS NOT NULL OR COALESCE(a.musicbrainz_id, a.lastfm_mbid) IS NOT NULL)
      AND (ic.content_hash IS NULL OR ic.artwork_source = 'deezer')
"""

_session = requests.Session()
_session.headers["User-Agent"] = "Rythmx/1.0"


def _download_image_bytes(url: str) -> bytes | None:
    if not url:
        return None
    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.content
    except requests.RequestException as exc:
        logger.debug("enrich_artist_art: image download failed %s: %s", url[:80], exc)
        return None


def _upsert_artist_cache(
    conn,
    *,
    artist_id: str,
    image_url: str,
    content_hash: str,
    artwork_source: str,
) -> None:
    conn.execute(
        """INSERT INTO image_cache
               (entity_type, entity_key, image_url, local_path, content_hash, artwork_source, last_accessed)
           VALUES ('artist', ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(entity_type, entity_key) DO UPDATE SET
               image_url=excluded.image_url,
               local_path=excluded.local_path,
               content_hash=excluded.content_hash,
               artwork_source=excluded.artwork_source,
               last_accessed=datetime('now')""",
        (artist_id, image_url, str(get_original_path(content_hash)), content_hash, artwork_source),
    )


def _process_item(conn, row):
    from app.services.image_service import deezer_get_artist_photo, fanart_get_artist

    artist_id = str(row["id"])
    artist_name = row["name"]
    mbid = row["artist_mbid"]
    deezer_id = row["deezer_artist_id"]
    current_hash = row["content_hash"]
    current_source = row["artwork_source"]

    # Upgrade path: try Fanart first when enabled and MBID is known.
    if config.FANART_API_KEY and mbid:
        fanart_url = fanart_get_artist(str(mbid))
        if fanart_url:
            payload = _download_image_bytes(fanart_url)
            if payload:
                content_hash = ingest(payload)
                _upsert_artist_cache(
                    conn,
                    artist_id=artist_id,
                    image_url=fanart_url,
                    content_hash=content_hash,
                    artwork_source="fanart",
                )
                conn.execute(
                    """UPDATE lib_artists
                       SET image_url_fanart = COALESCE(image_url_fanart, ?),
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (fanart_url, artist_id),
                )
                write_enrichment_meta(conn, "artist_art", "artist", artist_id, "found")
                logger.debug("enrich_artist_art: '%s' -> fanart", artist_name)
                return "found"

    # No fanart upgrade available. If we already have a deezer hash, keep it and skip.
    if current_hash and current_source == "deezer":
        write_enrichment_meta(conn, "artist_art", "artist", artist_id, "not_found")
        return "not_found"

    # Never downgrade an existing fanart source to deezer fallback.
    if current_source == "fanart":
        write_enrichment_meta(conn, "artist_art", "artist", artist_id, "not_found")
        return "not_found"

    # Fallback: Deezer photo (first-fill only; never downgrades fanart).
    if deezer_id:
        deezer_url = deezer_get_artist_photo(str(deezer_id))
        if deezer_url:
            payload = _download_image_bytes(deezer_url)
            if payload:
                content_hash = ingest(payload)
                _upsert_artist_cache(
                    conn,
                    artist_id=artist_id,
                    image_url=deezer_url,
                    content_hash=content_hash,
                    artwork_source="deezer",
                )
                conn.execute(
                    """UPDATE lib_artists
                       SET image_url_deezer = COALESCE(image_url_deezer, ?),
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (deezer_url, artist_id),
                )
                write_enrichment_meta(conn, "artist_art", "artist", artist_id, "found")
                logger.debug("enrich_artist_art: '%s' -> deezer", artist_name)
                return "found"

    write_enrichment_meta(conn, "artist_art", "artist", artist_id, "not_found")
    logger.debug("enrich_artist_art: '%s' -> not found", artist_name)
    return "not_found"


def enrich_artist_art(batch_size=100, stop_event=None, on_progress=None):
    """Stage 2b: artist artwork enrichment with source-aware local storage."""
    return run_enrichment_loop(
        worker_name="enrich_artist_art",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="artist_art",
        entity_type="artist",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
