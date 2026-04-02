"""
forge.py — The Forge pipeline-history endpoint.

All SQL uses ? placeholders. Router registered at /api/v1 in main.py.
"""
import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key
from app.services.forge import discovery_runner, new_music_runner

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


def _error(message: str, status_code: int = 400, code: str | None = None) -> JSONResponse:
    payload: dict[str, str] = {"status": "error", "message": message}
    if code:
        payload["code"] = code
    return JSONResponse(payload, status_code=status_code)


@router.get("/forge/pipeline-history")
def get_pipeline_history(
    pipeline_type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """Return recent pipeline_history rows, optionally filtered by pipeline_type."""
    runs = rythmx_store.get_pipeline_runs(pipeline_type=pipeline_type, limit=limit)
    return {"status": "ok", "runs": runs}


# ---------------------------------------------------------------------------
# New Music endpoints
# ---------------------------------------------------------------------------


@router.get("/forge/new-music/config")
def nm_get_config():
    """Return current New Music pipeline configuration."""
    cfg = new_music_runner.get_config()
    return {"status": "ok", "config": cfg}


@router.post("/forge/new-music/config")
def nm_save_config(data: Optional[dict[str, Any]] = Body(default=None)):
    """Save New Music pipeline configuration to app_settings."""
    data = data or {}
    error = new_music_runner.validate_config_updates(data)
    if error:
        return _error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    new_music_runner.save_config(data)
    return {"status": "ok"}


@router.post("/forge/new-music/run")
def nm_run(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Run the New Music pipeline.
    Optionally accepts config overrides in the request body.
    Returns the discovered releases and a summary.
    """
    config_override = data or {}
    error = new_music_runner.validate_config_updates(config_override)
    if error:
        return _error(error, status_code=400, code="FORGE_VALIDATION_ERROR")

    result_container: dict = {}
    error_container: dict = {}

    def _run():
        try:
            result_container["result"] = new_music_runner.run_new_music_pipeline(config_override or None)
        except Exception as exc:
            logger.error("new_music/run: pipeline error: %s", exc, exc_info=True)
            error_container["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True, name="nm-run")
    t.start()
    t.join(timeout=120)  # wait up to 2 min

    if t.is_alive():
        return _error("New Music pipeline timed out", status_code=504, code="FORGE_TIMEOUT")

    if error_container:
        return _error(error_container["error"], status_code=500, code="FORGE_RUN_FAILED")

    summary = result_container.get("result", {})

    # Fetch the stored results to return to the frontend
    releases = _get_discovered_releases()
    return {
        "status": "ok",
        "artists_checked": summary.get("artists_checked", 0),
        "neighbors_found": summary.get("neighbors_found", 0),
        "releases_found": summary.get("releases_found", 0),
        "releases": releases,
    }


@router.get("/forge/new-music/results")
def nm_get_results():
    """Return the last run's discovered releases from forge_discovered_releases."""
    releases = _get_discovered_releases()
    return {"status": "ok", "releases": releases}


def _get_discovered_releases() -> list[dict]:
    """
    Query forge_discovered_releases JOIN forge_discovered_artists.
    Adds in_library flag via LEFT JOIN on lib_artists.name_lower.
    Returns list of release dicts.
    """
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.artist_deezer_id,
                da.name        AS artist_name,
                r.title,
                r.record_type,
                r.release_date,
                r.cover_url,
                CASE WHEN la.id IS NOT NULL THEN 1 ELSE 0 END AS in_library
            FROM forge_discovered_releases r
            JOIN forge_discovered_artists da ON r.artist_deezer_id = da.deezer_id
            LEFT JOIN lib_artists la ON da.name_lower = la.name_lower
            ORDER BY r.release_date DESC, da.name ASC
            LIMIT 500
            """
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Discovery endpoints
# ---------------------------------------------------------------------------


@router.get("/forge/discovery/config")
def discovery_get_config():
    """Return current Forge Discovery configuration."""
    cfg = discovery_runner.get_config()
    return {"status": "ok", "config": cfg}


@router.post("/forge/discovery/config")
def discovery_save_config(data: Optional[dict[str, Any]] = Body(default=None)):
    """Save Forge Discovery configuration to app_settings."""
    data = data or {}
    error = discovery_runner.validate_config_updates(data)
    if error:
        return _error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    discovery_runner.save_config(data)
    return {"status": "ok"}


@router.post("/forge/discovery/run")
def discovery_run(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Run the Forge Discovery pipeline.
    Optionally accepts config overrides in the request body.
    """
    data = data or {}
    error = discovery_runner.validate_config_updates(data)
    if error:
        return _error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    try:
        summary = discovery_runner.run_discovery_pipeline(data or None)
    except Exception as exc:
        logger.error("forge/discovery/run: pipeline error: %s", exc, exc_info=True)
        return _error(str(exc), status_code=500, code="FORGE_DISCOVERY_FAILED")

    return {
        "status": "ok",
        "artists_found": summary.get("artists_found", 0),
        "artists": summary.get("artists", []),
    }


@router.get("/forge/discovery/results")
def discovery_get_results():
    """Return the latest Forge Discovery result set."""
    return {"status": "ok", "artists": discovery_runner.get_results()}
