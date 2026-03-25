"""
rich_deezer_artist.py — Stage 3 Deezer artist-level stats worker.

Requires: deezer_artist_id (from Stage 2a).
Writes: lib_artists.fans_deezer.
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, name, deezer_artist_id FROM lib_artists
    WHERE deezer_artist_id IS NOT NULL
      AND fans_deezer IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'deezer_artist_stats'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_artists
    WHERE deezer_artist_id IS NOT NULL
      AND fans_deezer IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'deezer_artist_stats'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.music_client import get_deezer_artist_info

    artist_id = row["id"]
    artist_name = row["name"]
    deezer_id = row["deezer_artist_id"]

    info = get_deezer_artist_info(deezer_id)
    if info and info.get("nb_fan") is not None:
        conn.execute(
            """
            UPDATE lib_artists
            SET fans_deezer = COALESCE(NULLIF(?, 0), fans_deezer),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (info["nb_fan"], artist_id),
        )
        write_enrichment_meta(conn, "deezer_artist_stats", "artist", artist_id, "found")
        logger.debug("enrich_deezer_artist: '%s' -> fans=%d", artist_name, info["nb_fan"])
        return "found"
    else:
        write_enrichment_meta(conn, "deezer_artist_stats", "artist", artist_id, "not_found")
        return "not_found"


def enrich_deezer_artist(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — Deezer artist stats: fan count per artist."""
    return run_enrichment_loop(
        worker_name="enrich_deezer_artist",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="deezer_artist_stats",
        entity_type="artist",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
