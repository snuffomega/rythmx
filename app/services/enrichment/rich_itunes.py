"""
rich_itunes.py — Stage 3 iTunes rich data worker: genre + release_date per album.

Requires: itunes_album_id (from Stage 2 enrich_library).
Writes: lib_albums.genre (COALESCE), lib_albums.release_date.
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, itunes_album_id, title FROM lib_albums
    WHERE itunes_album_id IS NOT NULL
      AND (genre IS NULL OR release_date IS NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'itunes_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_albums
    WHERE itunes_album_id IS NOT NULL
      AND (genre IS NULL OR release_date IS NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'itunes_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.music_client import get_album_itunes_rich

    album_id = row["id"]
    itunes_album_id = row["itunes_album_id"]
    album_title = row["title"]

    result = get_album_itunes_rich(itunes_album_id)
    if result and (result.get("genre") or result.get("release_date")):
        conn.execute(
            """
            UPDATE lib_albums
            SET genre = COALESCE(genre, ?),
                release_date = COALESCE(release_date, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result.get("genre") or None, result.get("release_date") or None, album_id),
        )
        write_enrichment_meta(conn, "itunes_rich", "album", album_id, "found")
        logger.debug("enrich_itunes_rich: '%s' -> genre=%s release=%s",
                     album_title, result.get("genre"), result.get("release_date"))
        return "found"
    else:
        write_enrichment_meta(conn, "itunes_rich", "album", album_id, "not_found")
        return "not_found"


def enrich_itunes_rich(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — iTunes rich data: genre + release_date per album."""
    return run_enrichment_loop(
        worker_name="enrich_itunes_rich",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="itunes_rich",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
