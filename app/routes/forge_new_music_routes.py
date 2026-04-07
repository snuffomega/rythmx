"""
Forge New Music routes.
"""
from __future__ import annotations

import logging
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

    try:
        summary = new_music_runner.run_new_music_pipeline(config_override or None)
    except Exception as exc:
        logger.error("new_music/run: pipeline error: %s", exc, exc_info=True)
        return facade._error(str(exc), status_code=500, code="FORGE_RUN_FAILED")

    releases = facade._get_discovered_releases()
    return {
        "status": "ok",
        "artists_checked": summary.get("artists_checked", 0),
        "neighbors_found": summary.get("neighbors_found", 0),
        "releases_found": summary.get("releases_found", 0),
        "prerelease_filtered_count": summary.get("prerelease_filtered_count", 0),
        "releases": releases,
        "filtered_releases": summary.get("filtered_releases", []),
        "prerelease_filtered_releases": summary.get("prerelease_filtered_releases", []),
    }


@router.get("/forge/new-music/results")
def nm_get_results():
    """Return the last run's discovered releases from forge_discovered_releases."""
    from app.routes import forge as facade

    releases = facade._get_discovered_releases()
    return {"status": "ok", "releases": releases}


@router.get("/forge/new-music/releases/{release_id}/tracks")
def nm_get_release_tracks(release_id: str):
    """
    Return track listing for a discovered release (currently Deezer album ID based).
    """
    from app.clients.music_client import get_album_tracks_deezer
    from app.routes import forge as facade

    release_id = str(release_id or "").strip()
    if not release_id:
        return facade._error("release_id is required", status_code=400, code="FORGE_VALIDATION_ERROR")

    try:
        tracks = get_album_tracks_deezer(release_id)
    except Exception as exc:
        logger.error("new_music/release_tracks: failed for release_id=%s: %s", release_id, exc, exc_info=True)
        return facade._error("Failed to load release tracks", status_code=502, code="FORGE_RELEASE_TRACKS_FAILED")

    return {
        "status": "ok",
        "release_id": release_id,
        "source": "deezer",
        "sources": [
            {
                "provider": "deezer",
                "url": f"https://www.deezer.com/album/{release_id}",
            }
        ],
        "tracks": tracks or [],
    }


@router.post("/forge/new-music/clear")
def nm_clear():
    """Clear all discovered releases and artists (Tier 2, rebuildable)."""
    from app.routes import forge as facade

    with facade.rythmx_store._connect() as conn:
        conn.execute("DELETE FROM forge_discovered_releases")
        conn.execute("DELETE FROM forge_discovered_artists")
    logger.info("new_music: manually cleared forge_discovered tables")
    return {"status": "ok"}
