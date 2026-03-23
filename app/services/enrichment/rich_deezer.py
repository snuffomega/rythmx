"""
rich_deezer.py — Stage 3 Deezer release data worker: record_type_deezer + thumb_url.

Requires: deezer_id (from Stage 2 enrich_library).
Writes: lib_albums.record_type_deezer (COALESCE), lib_albums.thumb_url_deezer (per-source column).
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, deezer_id, title FROM lib_albums
    WHERE deezer_id IS NOT NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'deezer_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_albums
    WHERE deezer_id IS NOT NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'deezer_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.music_client import get_deezer_album_info

    album_id = row["id"]
    deezer_id = row["deezer_id"]
    album_title = row["title"]

    result = get_deezer_album_info(deezer_id)
    if result:
        conn.execute(
            """
            UPDATE lib_albums
            SET record_type_deezer = COALESCE(record_type_deezer, ?),
                thumb_url_deezer = COALESCE(thumb_url_deezer, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result.get("record_type") or None,
             result.get("thumb_url") or None,
             album_id),
        )
        write_enrichment_meta(conn, "deezer_rich", "album", album_id, "found")
        logger.debug("enrich_deezer_release: '%s' -> record_type_deezer=%s thumb=%s",
                     album_title, result.get("record_type"), bool(result.get("thumb_url")))
        return "found"
    else:
        write_enrichment_meta(conn, "deezer_rich", "album", album_id, "not_found")
        return "not_found"


def enrich_deezer_release(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — Deezer release data: record_type_deezer + thumb_url per album."""
    return run_enrichment_loop(
        worker_name="enrich_deezer_release",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="deezer_rich",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
