"""
Forge New Music routes.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, Body

from app.services.forge import new_music_runner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/forge/new-music/config")
def nm_get_config():
    """Return current New Music pipeline configuration."""
    cfg = new_music_runner.get_config()
    return {"status": "ok", "config": cfg}


@router.post("/forge/new-music/config")
def nm_save_config(data: Optional[dict[str, Any]] = Body(default=None)):
    """Save New Music pipeline configuration to app_settings."""
    from app.routes import forge as facade

    data = data or {}
    error = new_music_runner.validate_config_updates(data)
    if error:
        return facade._error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    new_music_runner.save_config(data)
    return {"status": "ok"}


@router.post("/forge/new-music/run")
def nm_run(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Run the New Music pipeline.
    Optionally accepts config overrides in the request body.
    Returns the discovered releases and a summary.
    """
    from app.routes import forge as facade

    config_override = data or {}
    error = new_music_runner.validate_config_updates(config_override)
    if error:
        return facade._error(error, status_code=400, code="FORGE_VALIDATION_ERROR")

    result_container: dict[str, Any] = {}
    error_container: dict[str, str] = {}

    def _run():
        try:
            result_container["result"] = new_music_runner.run_new_music_pipeline(config_override or None)
        except Exception as exc:
            logger.error("new_music/run: pipeline error: %s", exc, exc_info=True)
            error_container["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True, name="nm-run")
    t.start()
    t.join(timeout=120)

    if t.is_alive():
        return facade._error("New Music pipeline timed out", status_code=504, code="FORGE_TIMEOUT")

    if error_container:
        return facade._error(error_container["error"], status_code=500, code="FORGE_RUN_FAILED")

    summary = result_container.get("result", {})
    releases = facade._get_discovered_releases()
    return {
        "status": "ok",
        "artists_checked": summary.get("artists_checked", 0),
        "neighbors_found": summary.get("neighbors_found", 0),
        "releases_found": summary.get("releases_found", 0),
        "releases": releases,
        "filtered_releases": summary.get("filtered_releases", []),
    }


@router.get("/forge/new-music/results")
def nm_get_results():
    """Return the last run's discovered releases from forge_discovered_releases."""
    from app.routes import forge as facade

    releases = facade._get_discovered_releases()
    return {"status": "ok", "releases": releases}


@router.post("/forge/new-music/clear")
def nm_clear():
    """Clear all discovered releases and artists (Tier 2, rebuildable)."""
    from app.routes import forge as facade

    with facade.rythmx_store._connect() as conn:
        conn.execute("DELETE FROM forge_discovered_releases")
        conn.execute("DELETE FROM forge_discovered_artists")
    logger.info("new_music: manually cleared forge_discovered tables")
    return {"status": "ok"}

