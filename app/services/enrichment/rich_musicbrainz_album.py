"""
rich_musicbrainz_album.py — Stage 3 MusicBrainz album date worker.

Requires: musicbrainz_release_id on lib_albums (populated when audio files
carry MBID tags).  At time of writing this column is 0% populated for most
users, so the worker will report 0 eligible and exit immediately — this is
expected and correct behaviour.

Writes:
  lib_albums.musicbrainz_release_group_id  — the Release Group MBID
  lib_albums.original_release_date_musicbrainz — first-release-date from the
                                                  Release Group (YYYY, YYYY-MM,
                                                  or YYYY-MM-DD)
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT id, title, musicbrainz_release_id FROM lib_albums
    WHERE musicbrainz_release_id IS NOT NULL
      AND original_release_date_musicbrainz IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_albums
    WHERE musicbrainz_release_id IS NOT NULL
      AND original_release_date_musicbrainz IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.musicbrainz_client import get_release

    album_id = row["id"]
    album_title = row["title"]
    release_mbid = row["musicbrainz_release_id"]

    info = get_release(release_mbid)
    if info:
        rg_id = info.get("release_group_id") or None
        first_date = info.get("first_release_date") or None

        if rg_id or first_date:
            conn.execute(
                """
                UPDATE lib_albums
                SET musicbrainz_release_group_id       = COALESCE(musicbrainz_release_group_id, ?),
                    original_release_date_musicbrainz  = COALESCE(original_release_date_musicbrainz, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (rg_id, first_date, album_id),
            )
            write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "found")
            logger.debug(
                "enrich_musicbrainz_album_rich: '%s' -> rg_id=%s first_date=%s",
                album_title, rg_id, first_date,
            )
            return "found"

    write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "not_found")
    return "not_found"


def enrich_musicbrainz_album_rich(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — MusicBrainz album rich data: release_group_id, original_release_date."""
    return run_enrichment_loop(
        worker_name="enrich_musicbrainz_album_rich",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="musicbrainz_album_rich",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
