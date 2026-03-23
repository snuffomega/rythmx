"""
stats_lastfm.py — Stage 3 Last.fm listener/play count worker.

Requires: lastfm_mbid (from Stage 2 enrich_artist_ids_lastfm).
Writes: lib_artists.listener_count_lastfm, lib_artists.play_count_lastfm.
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, name, lastfm_mbid FROM lib_artists
    WHERE lastfm_mbid IS NOT NULL
      AND (listener_count_lastfm IS NULL OR play_count_lastfm IS NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'lastfm_stats'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_artists
    WHERE lastfm_mbid IS NOT NULL
      AND (listener_count_lastfm IS NULL OR play_count_lastfm IS NULL)
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'lastfm_stats'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.last_fm_client import get_artist_info_lastfm

    artist_id = row["id"]
    artist_name = row["name"]
    mbid = row["lastfm_mbid"]

    stats = get_artist_info_lastfm(mbid=mbid, name=artist_name)
    if stats:
        conn.execute(
            """
            UPDATE lib_artists
            SET listener_count_lastfm = ?,
                play_count_lastfm = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stats["listeners"], stats["playcount"], artist_id),
        )
        write_enrichment_meta(conn, "lastfm_stats", "artist", artist_id, "found")
        logger.debug("enrich_stats_lastfm: '%s' -> listeners=%d plays=%d",
                     artist_name, stats["listeners"], stats["playcount"])
        return "found"
    else:
        write_enrichment_meta(conn, "lastfm_stats", "artist", artist_id, "not_found")
        return "not_found"


def enrich_stats_lastfm(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — Last.fm listener/play count per artist."""
    return run_enrichment_loop(
        worker_name="enrich_stats_lastfm",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="lastfm_stats",
        entity_type="artist",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
