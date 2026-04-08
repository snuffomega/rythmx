"""
library_enrich.py — Unified enrichment pipeline routes.

Replaces 4 separate enrichment routes in settings.py:
  GET  /api/v1/library/enrich/status  — unified pipeline status from enrichment_meta
  POST /api/v1/library/enrich/full    — start full DAG pipeline via EnrichmentOrchestrator
  POST /api/v1/library/enrich/stop    — signal all workers to stop after current batch
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/library/enrich/status")
def enrich_status():
    """
    Return enrichment pipeline status using canonical worker keys.

    Response shape includes:
      - running / started_at / phase
      - workers: canonical worker key counters from enrichment_meta
      - substeps: Post-Processing checklist state
      - last_run: persisted summary from app_settings
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator
    from app.db import rythmx_store
    from app.services.enrichment.runner import PipelineRunner

    orch = EnrichmentOrchestrator.get()
    running = orch.is_running()
    started_at = orch._started_at if running else None
    phase = rythmx_store.get_setting("pipeline_phase") if running else None

    try:
        workers = PipelineRunner.read_worker_snapshot()
    except Exception as e:
        logger.error("enrich_status: worker snapshot failed: %s", e)
        return JSONResponse(
            {"status": "error", "message": "Failed to load enrichment status"}, status_code=500
        )

    last_run = None
    started = rythmx_store.get_setting("last_run_started_at")
    ended = rythmx_store.get_setting("last_run_ended_at")
    outcome = rythmx_store.get_setting("last_run_outcome")
    if started and ended and outcome:
        duration_raw = rythmx_store.get_setting("last_run_duration_s", "0") or "0"
        enriched_raw = rythmx_store.get_setting("last_run_enriched", "0") or "0"
        not_found_raw = rythmx_store.get_setting("last_run_not_found", "0") or "0"
        try:
            duration_s = int(duration_raw)
        except (TypeError, ValueError):
            duration_s = 0
        try:
            enriched = int(enriched_raw)
        except (TypeError, ValueError):
            enriched = 0
        try:
            not_found = int(not_found_raw)
        except (TypeError, ValueError):
            not_found = 0
        last_run = {
            "started_at": started,
            "ended_at": ended,
            "duration_s": duration_s,
            "outcome": outcome,
            "enriched": enriched,
            "not_found": not_found,
        }

    substeps = getattr(orch, "current_substeps", {}) or {}

    return {
        "status": "ok",
        "running": running,
        "started_at": started_at,
        "phase": phase,
        "workers": workers,
        "substeps": substeps,
        "last_run": last_run,
    }


@router.post("/library/enrich/full")
def enrich_full(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Start the full enrichment pipeline (Stage 2 → Stage 3 → BPM).
    Returns 202 immediately; pipeline runs in background.

    Body (optional JSON): { "batch_size": int (1–200, default 50) }
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator

    data = data or {}
    batch_size = data.get("batch_size", 10_000)

    if not isinstance(batch_size, int) or batch_size < 1:
        return JSONResponse(
            {"status": "error", "message": "batch_size must be a positive integer"},
            status_code=400,
        )

    EnrichmentOrchestrator.get().run_full(batch_size=batch_size)
    return JSONResponse(
        {"status": "ok", "message": "Enrichment pipeline started"}, status_code=202
    )


@router.post("/library/enrich/musicbrainz_album")
def enrich_musicbrainz_album():
    """
    Manual trigger: enrich lib_albums with MusicBrainz Release Group ID and
    original first-release-date.

    Albums are eligible only when musicbrainz_release_id is populated (requires
    audio files tagged with MBID). Reports eligible count clearly so callers can
    distinguish "no work to do" (0 eligible) from an error.

    Response:
      { "status": "ok", "eligible": N, "message": "..." }
    """
    from app.db.rythmx_store import _connect
    from app.services.enrichment.rich_musicbrainz_album import enrich_musicbrainz_album_rich

    try:
        with _connect() as conn:
            eligible = conn.execute(
                """
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
            ).fetchone()[0]
    except Exception as e:
        logger.error("enrich_musicbrainz_album: eligible query failed: %s", e)
        return JSONResponse(
            {"status": "error", "message": "DB query failed"}, status_code=500
        )

    if eligible == 0:
        return {
            "status": "ok",
            "eligible": 0,
            "message": (
                "No eligible albums — musicbrainz_release_id is not populated. "
                "Tag your audio files with MusicBrainz release IDs and re-sync the library."
            ),
        }

    result = enrich_musicbrainz_album_rich(batch_size=eligible)
    return {
        "status": "ok",
        "eligible": eligible,
        "enriched": result.get("enriched", 0),
        "skipped": result.get("skipped", 0),
        "failed": result.get("failed", 0),
        "remaining": result.get("remaining", 0),
        "message": f"MusicBrainz album enrichment complete ({result.get('enriched', 0)} enriched).",
    }


@router.post("/library/enrich/stop")
def enrich_stop():
    """
    Signal all enrichment workers to stop after their current batch.
    State is preserved — next run resumes from where it stopped.
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator

    orch = EnrichmentOrchestrator.get()
    if not orch.is_running():
        return {"status": "ok", "message": "No enrichment running"}

    orch.stop()
    return {"status": "ok", "message": "Stop signal sent"}


@router.get("/library/enrich/artwork-sources")
def enrich_artwork_sources():
    """
    Artist artwork source verification surface.

    Returns grouped image_cache counts by artwork_source plus total artists
    currently missing a local content hash.
    """
    from app.db.rythmx_store import _connect

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(ic.artwork_source, 'missing') AS artwork_source, COUNT(*) AS count
            FROM lib_artists a
            LEFT JOIN image_cache ic
                   ON ic.entity_type = 'artist' AND ic.entity_key = a.id
            WHERE a.removed_at IS NULL
            GROUP BY COALESCE(ic.artwork_source, 'missing')
            ORDER BY count DESC, artwork_source
            """
        ).fetchall()
        missing = conn.execute(
            """
            SELECT COUNT(*)
            FROM lib_artists a
            LEFT JOIN image_cache ic
                   ON ic.entity_type = 'artist' AND ic.entity_key = a.id
            WHERE a.removed_at IS NULL
              AND (ic.content_hash IS NULL OR ic.content_hash = '')
            """
        ).fetchone()[0]

    return {
        "status": "ok",
        "sources": [dict(r) for r in rows],
        "missing": missing,
    }


@router.post("/library/enrich/artist-art/retry")
def enrich_artist_art_retry(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Manual retry gate for artist artwork upgrades.

    Re-runs artist artwork enrichment for rows that are missing local hashes
    or currently using deezer fallback. Worker upgrades to fanart when found
    and never downgrades existing fanart.
    """
    from app.services.enrichment.art_artist import enrich_artist_art

    data = data or {}
    batch_size = data.get("batch_size", 500)
    if not isinstance(batch_size, int) or batch_size < 1:
        return JSONResponse(
            {"status": "error", "message": "batch_size must be a positive integer"},
            status_code=400,
        )

    result = enrich_artist_art(batch_size=batch_size)
    return {"status": "ok", **result}
