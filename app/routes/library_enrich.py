"""
library_enrich.py — Unified enrichment pipeline routes.

Replaces 4 separate enrichment routes in settings.py:
  GET  /api/v1/library/enrich/status  — unified pipeline status from enrichment_meta
  POST /api/v1/library/enrich/full    — start full DAG pipeline via EnrichmentOrchestrator
  POST /api/v1/library/enrich/stop    — signal all workers to stop after current batch
"""
import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

enrich_bp = Blueprint("enrich", __name__)


@enrich_bp.route("/library/enrich/status", methods=["GET"])
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
          "deezer_rich":    { ... },
          "spotify_id":     { ... },
          "spotify_genres": { ... },
          "lastfm_id":      { ... },
          "lastfm_tags":    { ... },
          "lastfm_stats":   { ... },
          "deezer_bpm":     { ... }
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
        return jsonify({"status": "error", "message": "DB query failed"}), 500

    workers: dict = {}
    for r in rows:
        src = r["source"]
        if src not in workers:
            workers[src] = {"found": 0, "not_found": 0, "errors": 0, "pending": 0}
        field = "errors" if r["status"] == "error" else r["status"]
        if field in workers[src]:
            workers[src][field] = r["cnt"]

    # Aggregate enrich_library sub-sources into single "library" key.
    # itunes_artist + deezer_artist = artist-level confidence validation rows
    # itunes + deezer = album-level enrichment rows
    # All four represent Identity Matching stage work.
    _lib_sources = {"itunes_artist", "deezer_artist", "itunes", "deezer"}
    lib_agg: dict = {"found": 0, "not_found": 0, "errors": 0, "pending": 0}
    for src in _lib_sources:
        if src in workers:
            for field in ("found", "not_found", "errors", "pending"):
                lib_agg[field] += workers.pop(src)[field]
    if any(lib_agg[f] > 0 for f in ("found", "not_found", "errors", "pending")):
        workers["library"] = lib_agg

    return jsonify({"status": "ok", "running": running, "started_at": started_at, "workers": workers})


@enrich_bp.route("/library/enrich/full", methods=["POST"])
def enrich_full():
    """
    Start the full enrichment pipeline (Stage 2 → Stage 3 → BPM).
    Returns 202 immediately; pipeline runs in background.

    Body (optional JSON): { "batch_size": int (1–200, default 50) }
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator

    data = request.get_json(silent=True) or {}
    batch_size = data.get("batch_size", 50)

    if not isinstance(batch_size, int) or not (1 <= batch_size <= 200):
        return jsonify({"status": "error", "message": "batch_size must be integer 1–200"}), 400

    EnrichmentOrchestrator.get().run_full(batch_size=batch_size)
    return jsonify({"status": "ok", "message": "Enrichment pipeline started"}), 202


@enrich_bp.route("/library/enrich/stop", methods=["POST"])
def enrich_stop():
    """
    Signal all enrichment workers to stop after their current batch.
    State is preserved — next run resumes from where it stopped.
    """
    from app.services.api_orchestrator import EnrichmentOrchestrator

    orch = EnrichmentOrchestrator.get()
    if not orch.is_running():
        return jsonify({"status": "ok", "message": "No enrichment running"})

    orch.stop()
    return jsonify({"status": "ok", "message": "Stop signal sent"})
