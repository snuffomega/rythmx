"""
id_lastfm.py — Stage 2 Last.fm MBID worker.

Validates artist identity via album catalog overlap and stores lastfm_mbid.
No tag fetching — tags belong in Stage 3 (tags_lastfm).
"""
import logging
import threading

from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.enrichment._helpers import strip_title_suffixes, validate_artist

logger = logging.getLogger(__name__)


def enrich_artist_ids_lastfm(batch_size: int = 50, stop_event: threading.Event | None = None,
                              on_progress: "callable | None" = None) -> dict:
    """Stage 2 — Last.fm MBID Worker: validate + store lastfm_mbid only."""
    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name FROM lib_artists
                WHERE lastfm_mbid IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source IN ('lastfm_artist', 'lastfm_id')
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_artist_ids_lastfm: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
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
            lib_titles = [
                strip_title_suffixes(r["local_title"] or r["title"])
                for r in conn.execute(
                    "SELECT title, local_title FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL",
                    (artist_id,),
                ).fetchall()
            ]

            val = validate_artist(artist_name, lib_titles, "lastfm")
            if val and val["confidence"] >= 70:
                mbid = val["artist_id"]
                needs_verification = 1 if val["confidence"] < 85 else 0
                conn.execute(
                    """
                    UPDATE lib_artists
                    SET lastfm_mbid = ?,
                        needs_verification = CASE WHEN ? = 1 THEN 1 ELSE needs_verification END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND lastfm_mbid IS NULL
                    """,
                    (mbid, needs_verification, artist_id),
                )
                write_enrichment_meta(conn, "lastfm_artist", "artist", artist_id,
                                      "found", confidence=val["confidence"])
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                logger.debug("enrich_artist_ids_lastfm: '%s' -> mbid=%s conf=%d",
                             artist_name, mbid, val["confidence"])
            else:
                write_enrichment_meta(conn, "lastfm_artist", "artist", artist_id,
                                      "not_found", confidence=0)
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_artist_ids_lastfm: failed for '%s': %s", artist_name, e)
            write_enrichment_meta(conn, "lastfm_artist", "artist", artist_id,
                                  "error", error_msg=str(e)[:200])
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE lastfm_mbid IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source IN ('lastfm_artist', 'lastfm_id')
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_artist_ids_lastfm: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}
