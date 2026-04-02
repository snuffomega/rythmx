"""
forge.py - Forge API endpoints.

Router is registered at /api/v1 in main.py.
"""
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app import config
from app.db import get_playlist_pusher
from app.db import rythmx_store
from app.dependencies import verify_api_key
from app.services.forge import discovery_runner, new_music_runner

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

_BUILD_SOURCES = {"new_music", "custom_discovery", "sync", "manual"}
_BUILD_STATUSES = {"queued", "building", "ready", "published", "failed"}


def _error(message: str, status_code: int = 400, code: str | None = None) -> JSONResponse:
    payload: dict[str, str] = {"status": "error", "message": message}
    if code:
        payload["code"] = code
    return JSONResponse(payload, status_code=status_code)


def _validate_build_payload(data: dict[str, Any]) -> str | None:
    source = str(data.get("source", "manual")).strip().lower()
    if source not in _BUILD_SOURCES:
        return f"source must be one of: {', '.join(sorted(_BUILD_SOURCES))}"

    status = str(data.get("status", "ready")).strip().lower()
    if status not in _BUILD_STATUSES:
        return f"status must be one of: {', '.join(sorted(_BUILD_STATUSES))}"

    track_list = data.get("track_list")
    if track_list is not None and not isinstance(track_list, list):
        return "track_list must be a list"

    summary = data.get("summary")
    if summary is not None and not isinstance(summary, dict):
        return "summary must be an object"

    return None


def _get_library_platform() -> str:
    platform = str(config.LIBRARY_PLATFORM or "plex").strip().lower()
    try:
        saved = rythmx_store.get_setting("library_platform")
        if saved:
            platform = str(saved).strip().lower()
    except Exception:
        pass
    return platform


def _extract_publish_track_ids(track_list: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in track_list:
        if not isinstance(item, dict):
            continue
        candidate = (
            item.get("track_id")
            or item.get("plex_rating_key")
            or item.get("navidrome_track_id")
        )
        tid = str(candidate or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    return ordered


def _push_playlist(pusher: Any, playlist_name: str, track_ids: list[str]) -> str | None:
    if hasattr(pusher, "push_playlist"):
        return pusher.push_playlist(playlist_name, track_ids)
    if hasattr(pusher, "create_or_update_playlist"):
        return pusher.create_or_update_playlist(playlist_name, track_ids)
    return None


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


# ---------------------------------------------------------------------------
# Builder endpoints
# ---------------------------------------------------------------------------


@router.get("/forge/builds")
def forge_builds_list(
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    if source and source not in _BUILD_SOURCES:
        return _error(
            f"source must be one of: {', '.join(sorted(_BUILD_SOURCES))}",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )
    builds = rythmx_store.list_forge_builds(source=source, limit=limit)
    return {"status": "ok", "builds": builds}


@router.get("/forge/builds/{build_id}")
def forge_builds_get(build_id: str):
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "build": build}


@router.post("/forge/builds")
def forge_builds_create(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    validation_error = _validate_build_payload(data)
    if validation_error:
        return _error(validation_error, status_code=400, code="FORGE_VALIDATION_ERROR")

    source = str(data.get("source", "manual")).strip().lower()
    status = str(data.get("status", "ready")).strip().lower()
    run_mode = str(data.get("run_mode")).strip().lower() if data.get("run_mode") else None
    track_list = data.get("track_list") or []
    summary = data.get("summary") or {}

    default_name = f"{source.replace('_', ' ').title()} Build {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    name = str(data.get("name") or "").strip() or default_name

    build = rythmx_store.create_forge_build(
        name=name,
        source=source,
        status=status,
        track_list=track_list,
        summary=summary,
        run_mode=run_mode,
        build_id=data.get("id"),
    )
    return {"status": "ok", "build": build}


@router.delete("/forge/builds/{build_id}")
def forge_builds_delete(build_id: str):
    deleted = rythmx_store.delete_forge_build(build_id)
    if not deleted:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "deleted": True}


@router.post("/forge/builds/{build_id}/publish")
def forge_builds_publish(build_id: str, data: Optional[dict[str, Any]] = Body(default=None)):
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    platform = _get_library_platform()
    if platform == "jellyfin":
        return _error(
            "Jellyfin publish is planned but not implemented yet.",
            status_code=501,
            code="FORGE_PUBLISH_NOT_IMPLEMENTED",
        )

    request_data = data or {}
    playlist_name = str(request_data.get("name") or build.get("name") or "").strip()
    if not playlist_name:
        playlist_name = f"Build {build_id}"

    track_ids = _extract_publish_track_ids(build.get("track_list") or [])
    if not track_ids:
        return _error(
            "Build has no publishable library track IDs (track_id / plex_rating_key / navidrome_track_id).",
            status_code=400,
            code="FORGE_PUBLISH_EMPTY",
        )

    pusher = get_playlist_pusher()
    try:
        platform_playlist_id = _push_playlist(pusher, playlist_name, track_ids)
    except Exception as exc:
        logger.error("forge/builds/%s/publish: pusher error: %s", build_id, exc, exc_info=True)
        return _error(str(exc), status_code=500, code="FORGE_PUBLISH_FAILED")

    if not platform_playlist_id:
        return _error(
            "Platform publish failed; check platform credentials/logs.",
            status_code=502,
            code="FORGE_PUBLISH_FAILED",
        )

    pushed_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    playlist = rythmx_store.upsert_forge_playlist(
        playlist_id=build_id,
        name=playlist_name,
        track_ids=track_ids,
        pushed_at=pushed_at,
    )
    rythmx_store.update_forge_build_status(build_id, "published")

    return {
        "status": "ok",
        "build_id": build_id,
        "playlist": playlist,
        "platform": platform,
        "platform_playlist_id": str(platform_playlist_id),
    }

