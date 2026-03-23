"""
art_artist.py — Artist photo enrichment worker.

Resolution order:
  1. Fanart.tv  (requires FANART_API_KEY + lastfm_mbid)
  2. Deezer     (requires deezer_id — free, no auth)

Writes: lib_artists.image_url_fanart, lib_artists.image_url_deezer
(per-source columns — resolved via COALESCE at query time)
"""
import logging

from app import config
from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, name, lastfm_mbid, deezer_artist_id FROM lib_artists
    WHERE image_url_fanart IS NULL AND image_url_deezer IS NULL
      AND removed_at IS NULL
      AND (deezer_artist_id IS NOT NULL OR lastfm_mbid IS NOT NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'artist_art'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_artists
    WHERE image_url_fanart IS NULL AND image_url_deezer IS NULL
      AND removed_at IS NULL
      AND (deezer_artist_id IS NOT NULL OR lastfm_mbid IS NOT NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'artist_art'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.services.image_service import fanart_get_artist, deezer_get_artist_photo

    artist_id = row["id"]
    artist_name = row["name"]
    mbid = row["lastfm_mbid"]
    deezer_id = row["deezer_artist_id"]

    fanart_url = ""
    deezer_url = ""

    # Tier 1: Fanart.tv (real artist photo — requires API key + MBID)
    if config.FANART_API_KEY and mbid:
        fanart_url = fanart_get_artist(mbid)

    # Tier 2: Deezer artist photo (free, no auth)
    if deezer_id:
        deezer_url = deezer_get_artist_photo(str(deezer_id))

    if fanart_url or deezer_url:
        conn.execute(
            """UPDATE lib_artists
               SET image_url_fanart = COALESCE(image_url_fanart, ?),
                   image_url_deezer = COALESCE(image_url_deezer, ?),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (fanart_url or None, deezer_url or None, artist_id),
        )
        write_enrichment_meta(conn, "artist_art", "artist", artist_id, "found")
        best = fanart_url or deezer_url
        logger.debug("enrich_artist_art: '%s' -> %s", artist_name, best[:60])
        return "found"
    else:
        write_enrichment_meta(conn, "artist_art", "artist", artist_id, "not_found")
        logger.debug("enrich_artist_art: '%s' -> not found", artist_name)
        return "not_found"


def enrich_artist_art(batch_size=100, stop_event=None, on_progress=None):
    """Phase 1.8 — Artist photo enrichment: Fanart.tv → Deezer fallback."""
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
