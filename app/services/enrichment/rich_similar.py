"""
rich_similar.py — Stage 3 Similar Artists worker.

Two-source strategy:
  1. Last.fm artist.getSimilar (primary) — uses lastfm_mbid or name lookup
  2. Deezer /artist/{id}/related (fallback) — free, no auth

Requires: lastfm_mbid OR deezer_artist_id (at least one).
Writes: lib_artists.similar_artists_json (JSON array, top 10).
"""
import json
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_MAX_SIMILAR = 10

_CANDIDATE_SQL = """
    SELECT id, name, lastfm_mbid, deezer_artist_id FROM lib_artists
    WHERE (lastfm_mbid IS NOT NULL OR deezer_artist_id IS NOT NULL)
      AND similar_artists_json IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'similar_artists'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_artists
    WHERE (lastfm_mbid IS NOT NULL OR deezer_artist_id IS NOT NULL)
      AND similar_artists_json IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'artist' AND source = 'similar_artists'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.last_fm_client import get_similar_artists
    from app.clients.music_client import get_deezer_related_artists

    artist_id = row["id"]
    artist_name = row["name"]

    merged: list[dict] = []
    seen_names: set[str] = set()

    # Primary: Last.fm (has match scores)
    try:
        lfm_results = get_similar_artists(artist_name, limit=_MAX_SIMILAR)
        for r in lfm_results:
            name_lower = r["name"].lower()
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                merged.append({
                    "name": r["name"],
                    "match": round(r.get("match", 0), 3),
                    "source": "lastfm",
                })
    except Exception as e:
        logger.debug("rich_similar: Last.fm failed for '%s': %s", artist_name, e)

    # Fallback: Deezer (fill gaps up to _MAX_SIMILAR)
    deezer_id = row["deezer_artist_id"]
    if deezer_id and len(merged) < _MAX_SIMILAR:
        try:
            dz_results = get_deezer_related_artists(deezer_id, limit=_MAX_SIMILAR)
            for r in dz_results:
                name_lower = r["name"].lower()
                if name_lower not in seen_names and len(merged) < _MAX_SIMILAR:
                    seen_names.add(name_lower)
                    merged.append({
                        "name": r["name"],
                        "match": 0,
                        "source": "deezer",
                    })
        except Exception as e:
            logger.debug("rich_similar: Deezer failed for '%s': %s", artist_name, e)

    if merged:
        json_str = json.dumps(merged[:_MAX_SIMILAR], ensure_ascii=False)
        conn.execute(
            """
            UPDATE lib_artists
            SET similar_artists_json = COALESCE(similar_artists_json, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json_str, artist_id),
        )
        write_enrichment_meta(conn, "similar_artists", "artist", artist_id, "found")
        logger.debug("enrich_similar: '%s' -> %d similar artists", artist_name, len(merged))
        return "found"
    else:
        write_enrichment_meta(conn, "similar_artists", "artist", artist_id, "not_found")
        return "not_found"


def enrich_similar_artists(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — Similar Artists: Last.fm primary + Deezer fallback."""
    return run_enrichment_loop(
        worker_name="enrich_similar_artists",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="similar_artists",
        entity_type="artist",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )
