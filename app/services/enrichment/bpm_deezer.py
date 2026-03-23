"""
bpm_deezer.py — Deezer BPM enrichment worker + status.

INACTIVE — Manually triggered only via POST /api/v1/library/enrich-deezer-bpm.
Intentionally excluded from PipelineRunner Stage 3 due to rate-limit profile:
  2-pass per album (1 GET /album/{id}/tracks + 1 GET /track/{id} per track).
  A typical 12-track album = 13 Deezer API calls.
  111 albums × 13 = ~1,400+ calls — dwarfs all other Deezer enrichment combined.
  All calls share the DomainRateLimiter("deezer") bucket, risking 429s mid-pipeline.

When Forge rebuilds the pipeline, BPM can be reconsidered with a dedicated
rate-limit bucket or per-request throttle that doesn't block other Deezer workers.

Two-pass per album: GET /album/{id}/tracks for track IDs,
then GET /track/{id} per track for BPM (not included in album track list).
"""
import logging

from app.db import rythmx_store
from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)

_DEEZER_ALBUM_URL = "https://api.deezer.com/album/{album_id}/tracks"
_DEEZER_TRACK_URL = "https://api.deezer.com/track/{track_id}"


def _deezer_rate_limited_get(url: str) -> dict | None:
    """Single rate-limited GET to Deezer via shared DomainRateLimiter. Returns parsed JSON or None."""
    import requests

    rate_limiter.acquire("deezer")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 429:
            rate_limiter.record_429("deezer")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("deezer")
        return resp.json()
    except Exception as e:
        logger.debug("Deezer request failed for %s: %s", url, e)
        return None


def _fetch_deezer_album_tracks(deezer_album_id: str) -> list[dict]:
    """
    Fetch BPM for all tracks in a Deezer album.

    Two-pass: first GET /album/{id}/tracks for track IDs, then GET /track/{id}
    per track for BPM (bpm is not included in the album tracks list response).

    Returns list of {title, bpm} dicts. Empty on error or no tracks.
    """
    # Pass 1: get track IDs from album endpoint
    data = _deezer_rate_limited_get(_DEEZER_ALBUM_URL.format(album_id=deezer_album_id))
    if not data:
        return []
    track_stubs = data.get("data", [])
    if not track_stubs:
        return []

    # Pass 2: fetch each track individually to get BPM
    results = []
    for stub in track_stubs:
        track_id = stub.get("id")
        title = stub.get("title", "")
        if not track_id:
            continue
        track_data = _deezer_rate_limited_get(_DEEZER_TRACK_URL.format(track_id=track_id))
        if not track_data:
            continue
        bpm = float(track_data.get("bpm", 0) or 0)
        if bpm > 0:
            results.append({"title": title, "bpm": bpm})

    return results


def enrich_deezer_bpm(batch_size: int = 30, stop_event=None,
                       on_progress=None) -> dict:
    """
    Deezer BPM enrichment pass.

    For each lib_album with a deezer_id, fetch the Deezer track list and write
    bpm → lib_tracks.tempo_deezer using exact title match (title_lower).

    Only processes albums not already in enrichment_meta(source='deezer_bpm').
    Resumable — interrupted runs pick up where they left off.

    Returns {enriched_tracks, enriched_albums, failed, skipped, remaining}.
    """
    import json  # noqa: F401 — kept for consistency with original

    enriched_tracks = 0
    enriched_albums = 0
    failed = 0
    skipped = 0

    # Load albums that have a deezer_id but haven't been BPM-enriched yet
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT la.id, la.deezer_id, ar.name AS artist_name, la.title
                FROM lib_albums la
                JOIN lib_artists ar ON la.artist_id = ar.id
                WHERE la.deezer_id IS NOT NULL
                  AND la.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'deezer_bpm'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_deezer_bpm: could not read lib_albums: %s", e)
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": -1, "error": str(e)}

    if not rows:
        logger.info("enrich_deezer_bpm: nothing to enrich")
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": 0}

    for album in rows:
        if stop_event and stop_event.is_set():
            break
        album_id = album["id"]
        deezer_album_id = album["deezer_id"]
        artist_name = album["artist_name"]
        album_title = album["title"]

        deezer_tracks = _fetch_deezer_album_tracks(deezer_album_id)

        try:
            conn = _connect()
        except Exception:
            failed += 1
            continue
        try:
            if not deezer_tracks:
                write_enrichment_meta(conn, "deezer_bpm", "album", album_id, "not_found")
                skipped += 1
                if on_progress:
                    on_progress(enriched_albums, skipped, failed, len(rows))
                continue

            # Build lookup: title_lower → bpm
            bpm_map = {t["title"].lower(): t["bpm"] for t in deezer_tracks}

            # Match lib_tracks for this album by title_lower
            lib_tracks = conn.execute(
                "SELECT id, title_lower FROM lib_tracks WHERE album_id = ?",
                (album_id,),
            ).fetchall()

            updated = 0
            for track in lib_tracks:
                bpm = bpm_map.get(track["title_lower"])
                if bpm:
                    conn.execute(
                        "UPDATE lib_tracks SET tempo_deezer = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (bpm, track["id"]),
                    )
                    updated += 1

            write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                   "found" if updated > 0 else "not_found")

            enriched_tracks += updated
            enriched_albums += 1
            if on_progress:
                on_progress(enriched_albums, skipped, failed, len(rows))
            logger.debug(
                "enrich_deezer_bpm: '%s / %s' → %d tracks updated",
                artist_name, album_title, updated,
            )
        except Exception as e:
            logger.warning("enrich_deezer_bpm: failed for '%s / %s': %s",
                           artist_name, album_title, e)
            try:
                write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                       "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched_albums, skipped, failed, len(rows))
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

    logger.info(
        "enrich_deezer_bpm: enriched_tracks=%d enriched_albums=%d failed=%d skipped=%d",
        enriched_tracks, enriched_albums, failed, skipped,
    )
    return {
        "enriched_tracks": enriched_tracks,
        "enriched_albums": enriched_albums,
        "failed": failed,
        "skipped": skipped,
        "remaining": len(rows),
    }


def get_deezer_bpm_status() -> dict:
    """
    Returns {enriched_albums, total_albums_with_deezer, enriched_tracks,
             total_tracks, last_run}.
    total_albums_with_deezer is the pool that can be enriched.
    """
    try:
        with _connect() as conn:
            total_albums = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE deezer_id IS NOT NULL"
            ).fetchone()[0]
            enriched_albums = conn.execute(
                """
                SELECT COUNT(*) FROM enrichment_meta
                WHERE source = 'deezer_bpm' AND entity_type = 'album'
                  AND status = 'found'
                """
            ).fetchone()[0]
            enriched_tracks = conn.execute(
                "SELECT COUNT(*) FROM lib_tracks WHERE tempo_deezer IS NOT NULL AND tempo_deezer > 0"
            ).fetchone()[0]
            total_tracks = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()[0]
    except Exception:
        total_albums = enriched_albums = enriched_tracks = total_tracks = 0

    last_run = rythmx_store.get_setting("deezer_bpm_last_run")
    return {
        "enriched_albums": enriched_albums,
        "total_albums": total_albums,
        "enriched_tracks": enriched_tracks,
        "total_tracks": total_tracks,
        "last_run": last_run,
    }
