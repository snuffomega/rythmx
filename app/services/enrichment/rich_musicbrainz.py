"""
rich_musicbrainz.py — Stage 3 MusicBrainz rich data worker.

Requires: musicbrainz_id (from Stage 2b id_musicbrainz).
Writes: lib_artists.area_musicbrainz, begin_area_musicbrainz, formed_year_musicbrainz.
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, name, musicbrainz_id FROM lib_artists
    WHERE musicbrainz_id IS NOT NULL
      AND (area_musicbrainz IS NULL OR begin_area_musicbrainz IS NULL)
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'musicbrainz_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_artists
    WHERE musicbrainz_id IS NOT NULL
      AND (area_musicbrainz IS NULL OR begin_area_musicbrainz IS NULL)
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'musicbrainz_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.musicbrainz_client import get_artist

    artist_id = row["id"]
    artist_name = row["name"]
    mbid = row["musicbrainz_id"]

    info = get_artist(mbid)
    if info:
        area = info.get("area") or None
        begin_area = info.get("begin_area") or None
        formed_year = info.get("formed_year")

        if area or begin_area or formed_year:
            conn.execute(
                """
                UPDATE lib_artists
                SET area_musicbrainz       = COALESCE(area_musicbrainz, ?),
                    begin_area_musicbrainz = COALESCE(begin_area_musicbrainz, ?),
                    formed_year_musicbrainz = COALESCE(formed_year_musicbrainz, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (area, begin_area, formed_year, artist_id),
            )
            write_enrichment_meta(conn, "musicbrainz_rich", "artist", artist_id, "found")
            logger.debug(
                "enrich_musicbrainz_rich: '%s' -> area=%s begin_area=%s formed=%s",
                artist_name, area, begin_area, formed_year,
            )
            return "found"

    write_enrichment_meta(conn, "musicbrainz_rich", "artist", artist_id, "not_found")
    return "not_found"


def enrich_musicbrainz_rich(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — MusicBrainz rich data: area, begin_area, formed_year."""
    return run_enrichment_loop(
        worker_name="enrich_musicbrainz_rich",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="musicbrainz_rich",
        entity_type="artist",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
