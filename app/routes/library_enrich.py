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
    Returns unified pipeline status grouped by enrichment_meta source.

    enrich_library writes 4 sub-sources to enrichment_meta:
      itunes_artist  — artist-level iTunes confidence validation
      deezer_artist  — artist-level Deezer confidence validation
      itunes         — album-level iTunes ID enrichment
      deezer         — album-level Deezer ID enrichment
    These are aggregated into a single "library" key so the Identity Matching
    stage card shows one complete number reflecting all enrich_library work.

    Response:
      {
        "status": "ok",
        "running": bool,
        "started_at": "ISO8601 UTC string | null",
        "workers": {
          "library":        { "found": int, "not_found": int, "errors": int, "pending": int },
          "itunes_rich":    { ... },
          ...
        }
      }
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator
    from app.db.rythmx_store import _connect

    orch = EnrichmentOrchestrator.get()
    running = orch.is_running()
    started_at = orch._started_at if running else None

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT source, status, COUNT(*) AS cnt
                FROM enrichment_meta
                GROUP BY source, status
                """
            ).fetchall()
    except Exception as e:
        logger.error("enrich_status: DB query failed: %s", e)
        return JSONResponse(
            {"status": "error", "message": "DB query failed"}, status_code=500
        )

    workers: dict = {}
    for r in rows:
        src = r["source"]
        if src not in workers:
            workers[src] = {"found": 0, "not_found": 0, "errors": 0, "pending": 0}
        field = "errors" if r["status"] == "error" else r["status"]
        if field in workers[src]:
            workers[src][field] = r["cnt"]

    # Aggregate enrich_library sub-sources into single "library" key.
    _lib_sources = {"itunes_artist", "deezer_artist", "itunes", "deezer"}
    lib_agg: dict = {"found": 0, "not_found": 0, "errors": 0, "pending": 0}
    for src in _lib_sources:
        if src in workers:
            entry = workers.pop(src)
            for field in ("found", "not_found", "errors", "pending"):
                lib_agg[field] += entry[field]
    if any(lib_agg[f] > 0 for f in ("found", "not_found", "errors", "pending")):
        workers["library"] = lib_agg

    return {"status": "ok", "running": running, "started_at": started_at, "workers": workers}


@router.post("/library/enrich/full")
def enrich_full(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Start the full enrichment pipeline (Stage 2 → Stage 3 → BPM).
    Returns 202 immediately; pipeline runs in background.

    Body (optional JSON): { "batch_size": int (1–200, default 50) }
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator

    data = data or {}
    batch_size = data.get("batch_size", 50)

    if not isinstance(batch_size, int) or not (1 <= batch_size <= 200):
        return JSONResponse(
            {"status": "error", "message": "batch_size must be integer 1–200"},
            status_code=400,
        )

    EnrichmentOrchestrator.get().run_full(batch_size=batch_size)
    return JSONResponse(
        {"status": "ok", "message": "Enrichment pipeline started"}, status_code=202
    )


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
