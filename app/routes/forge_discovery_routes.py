"""
Forge Discovery routes.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body

from app.services.forge import discovery_runner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/forge/discovery/config")
def discovery_get_config():
    """Return current Forge Discovery configuration."""
    cfg = discovery_runner.get_config()
    return {"status": "ok", "config": cfg}


@router.post("/forge/discovery/config")
def discovery_save_config(data: Optional[dict[str, Any]] = Body(default=None)):
    """Save Forge Discovery configuration to app_settings."""
    from app.routes import forge as facade

    data = data or {}
    error = discovery_runner.validate_config_updates(data)
    if error:
        return facade._error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    discovery_runner.save_config(data)
    return {"status": "ok"}


@router.post("/forge/discovery/run")
def discovery_run(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Run the Forge Discovery pipeline.
    Optionally accepts config overrides in the request body.
    """
    from app.routes import forge as facade

    data = data or {}
    error = discovery_runner.validate_config_updates(data)
    if error:
        return facade._error(error, status_code=400, code="FORGE_VALIDATION_ERROR")
    try:
        summary = discovery_runner.run_discovery_pipeline(data or None)
    except Exception as exc:
        logger.error("forge/discovery/run: pipeline error: %s", exc, exc_info=True)
        return facade._error(str(exc), status_code=500, code="FORGE_DISCOVERY_FAILED")

    payload: dict[str, Any] = {"status": "ok"}
    if isinstance(summary, dict):
        payload.update(summary)
    payload["artists_found"] = int(payload.get("artists_found") or len(payload.get("artists") or []))
    payload["artists"] = payload.get("artists") or []
    return payload


@router.get("/forge/discovery/results")
def discovery_get_results():
    """Return the latest Forge Discovery result set."""
    return {"status": "ok", "artists": discovery_runner.get_results()}

