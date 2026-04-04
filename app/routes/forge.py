"""
forge.py - Forge API endpoints.

Router is registered at /api/v1 in main.py.
"""
import logging
import threading
from urllib.parse import urlparse
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app import config
from app.db import get_playlist_pusher
from app.db import rythmx_store
from app.dependencies import verify_api_key
from app.services import playlist_importer
from app.services.forge import discovery_runner, new_music_runner

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

_BUILD_SOURCES = {"new_music", "custom_discovery", "sync", "manual"}
_BUILD_STATUSES = {"queued", "building", "ready", "published", "failed"}
_BUILD_RUN_MODES = {"build", "fetch"}


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


def _validate_build_update_payload(data: dict[str, Any]) -> str | None:
    allowed = {"name", "status", "run_mode", "track_list", "summary"}
    unknown = sorted(k for k in data.keys() if k not in allowed)
    if unknown:
        return f"unknown fields: {', '.join(unknown)}"

    if "name" in data and data.get("name") is not None and not isinstance(data.get("name"), str):
        return "name must be a string"

    if "status" in data and data.get("status") is not None:
        status = str(data.get("status")).strip().lower()
        if status not in _BUILD_STATUSES:
            return f"status must be one of: {', '.join(sorted(_BUILD_STATUSES))}"

    if "run_mode" in data and data.get("run_mode") is not None:
        run_mode = str(data.get("run_mode")).strip().lower()
        if run_mode not in _BUILD_RUN_MODES:
            return f"run_mode must be one of: {', '.join(sorted(_BUILD_RUN_MODES))}"

    if "track_list" in data and data.get("track_list") is not None and not isinstance(data.get("track_list"), list):
        return "track_list must be a list"

    if "summary" in data and data.get("summary") is not None and not isinstance(data.get("summary"), dict):
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
    def _normalize_candidate(value: Any) -> str:
        if isinstance(value, dict):
            value = (
                value.get("id")
                or value.get("track_id")
                or value.get("plex_rating_key")
                or value.get("navidrome_track_id")
            )
        return str(value or "").strip()

    seen: set[str] = set()
    ordered: list[str] = []
    for item in track_list:
        if not isinstance(item, dict):
            continue
        tid = (
            _normalize_candidate(item.get("track_id"))
            or _normalize_candidate(item.get("plex_rating_key"))
            or _normalize_candidate(item.get("navidrome_track_id"))
        )
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


def _sync_library_playlist_cache(
    *,
    playlist_id: str,
    playlist_name: str,
    platform: str,
    track_ids: list[str],
) -> dict[str, Any]:
    """
    Upsert a published playlist into lib_playlists/lib_playlist_tracks so it is
    immediately visible in Library UX without waiting for a full sync run.
    """
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for raw in track_ids:
        tid = str(raw or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered_ids.append(tid)

    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    with rythmx_store._connect() as conn:
        durations: dict[str, int] = {}
        if ordered_ids:
            placeholders = ",".join("?" for _ in ordered_ids)
            rows = conn.execute(
                f"SELECT id, duration FROM lib_tracks WHERE id IN ({placeholders})",
                tuple(ordered_ids),
            ).fetchall()
            durations = {str(r["id"]): int(r["duration"] or 0) for r in rows}

        publishable_ids = [tid for tid in ordered_ids if tid in durations]
        duration_ms = sum(durations[tid] for tid in publishable_ids)

        conn.execute(
            """
            INSERT OR REPLACE INTO lib_playlists
                (id, name, source_platform, cover_url, track_count, duration_ms, updated_at, synced_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                playlist_id,
                playlist_name,
                platform,
                len(publishable_ids),
                duration_ms,
                now,
            ),
        )
        conn.execute("DELETE FROM lib_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        for pos, track_id in enumerate(publishable_ids):
            conn.execute(
                """
                INSERT INTO lib_playlist_tracks (playlist_id, track_id, position)
                VALUES (?, ?, ?)
                """,
                (playlist_id, track_id, pos),
            )

    return {
        "id": playlist_id,
        "name": playlist_name,
        "source_platform": platform,
        "track_count": len(publishable_ids),
        "duration_ms": duration_ms,
    }


def _detect_sync_source(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    if "spotify.com" in host or source_url.strip().startswith("spotify:"):
        return "spotify"
    if "last.fm" in host:
        return "lastfm"
    if "deezer.com" in host:
        return "deezer"
    return None


def _import_sync_source(source: str, source_url: str) -> dict:
    if source == "spotify":
        return playlist_importer.import_from_spotify(source_url)
    if source == "lastfm":
        return playlist_importer.import_from_lastfm(source_url)
    if source == "deezer":
        return playlist_importer.import_from_deezer(source_url)
    return {"status": "error", "message": f"Unsupported source '{source}'"}


def _shape_sync_track(track: dict[str, Any]) -> dict[str, Any]:
    raw_track_id = (
        track.get("track_id")
        or track.get("plex_rating_key")
        or track.get("navidrome_track_id")
    )
    if isinstance(raw_track_id, dict):
        raw_track_id = (
            raw_track_id.get("id")
            or raw_track_id.get("track_id")
            or raw_track_id.get("plex_rating_key")
            or raw_track_id.get("navidrome_track_id")
        )

    return {
        "track_id": str(raw_track_id).strip() if raw_track_id is not None else None,
        "spotify_track_id": track.get("spotify_track_id", ""),
        "track_name": track.get("track_name", ""),
        "artist_name": track.get("artist_name", ""),
        "album_name": track.get("album_name", ""),
        "is_owned": bool(track.get("is_owned", False)),
    }


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
        "filtered_releases": summary.get("filtered_releases", []),
    }


@router.get("/forge/new-music/results")
def nm_get_results():
    """Return the last run's discovered releases from forge_discovered_releases."""
    releases = _get_discovered_releases()
    return {"status": "ok", "releases": releases}


@router.post("/forge/new-music/clear")
def nm_clear():
    """Clear all discovered releases and artists (Tier 2 — rebuildable)."""
    with rythmx_store._connect() as conn:
        conn.execute("DELETE FROM forge_discovered_releases")
        conn.execute("DELETE FROM forge_discovered_artists")
    logger.info("new_music: manually cleared forge_discovered tables")
    return {"status": "ok"}


def _get_discovered_releases() -> list[dict]:
    """
    Query forge_discovered_releases JOIN forge_discovered_artists.
    in_library is release-level: true only when this specific release exists in lib_releases.
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
                CASE WHEN lr.id IS NOT NULL THEN 1 ELSE 0 END AS in_library
            FROM forge_discovered_releases r
            JOIN forge_discovered_artists da ON r.artist_deezer_id = da.deezer_id
            LEFT JOIN lib_artists la ON da.name_lower = la.name_lower
            LEFT JOIN lib_releases lr
                ON lr.artist_id = la.id
                AND lower(trim(lr.title)) = lower(trim(r.title))
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


# ---------------------------------------------------------------------------
# Sync endpoints
# ---------------------------------------------------------------------------


@router.post("/forge/sync/load")
def forge_sync_load(data: Optional[dict[str, Any]] = Body(default=None)):
    payload = data or {}
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url:
        return _error(
            "source_url is required",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )

    source = str(payload.get("source") or "").strip().lower() or _detect_sync_source(source_url)
    if source not in {"spotify", "lastfm", "deezer"}:
        return _error(
            "Unable to detect source. Supported URLs: Spotify, Last.fm, Deezer.",
            status_code=400,
            code="FORGE_SYNC_UNSUPPORTED_SOURCE",
        )

    result = _import_sync_source(source, source_url)
    if result.get("status") != "ok":
        return _error(
            str(result.get("message") or "Sync load failed"),
            status_code=400,
            code="FORGE_SYNC_LOAD_FAILED",
        )

    shaped_tracks = [_shape_sync_track(t) for t in (result.get("tracks") or [])]
    total = int(result.get("track_count") or len(shaped_tracks))
    owned = int(result.get("owned_count") or sum(1 for t in shaped_tracks if t.get("is_owned")))
    missing = max(0, total - owned)
    queue_build = bool(payload.get("queue_build", True))

    build = None
    if queue_build:
        explicit_name = str(payload.get("name") or "").strip()
        default_name = str(result.get("name") or f"Sync {source.title()}").strip()
        build_name = explicit_name or default_name
        build = rythmx_store.create_forge_build(
            name=build_name,
            source="sync",
            status="ready",
            run_mode="build",
            track_list=shaped_tracks,
            summary={
                "source": source,
                "source_url": source_url,
                "track_count": total,
                "owned_count": owned,
                "missing_count": missing,
            },
        )

    return {
        "status": "ok",
        "source": source,
        "name": result.get("name"),
        "track_count": total,
        "owned_count": owned,
        "missing_count": missing,
        "queue_build": queue_build,
        "build": build,
        "tracks": shaped_tracks,
    }


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


@router.patch("/forge/builds/{build_id}")
def forge_builds_update(build_id: str, data: Optional[dict[str, Any]] = Body(default=None)):
    payload = data or {}
    validation_error = _validate_build_update_payload(payload)
    if validation_error:
        return _error(validation_error, status_code=400, code="FORGE_VALIDATION_ERROR")

    updated = rythmx_store.update_forge_build(
        build_id,
        name=payload.get("name") if "name" in payload else None,
        status=payload.get("status") if "status" in payload else None,
        run_mode=payload.get("run_mode") if "run_mode" in payload else None,
        track_list=payload.get("track_list") if "track_list" in payload else None,
        summary=payload.get("summary") if "summary" in payload else None,
    )
    if not updated:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "build": updated}


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

    library_playlist = _sync_library_playlist_cache(
        playlist_id=str(platform_playlist_id),
        playlist_name=playlist_name,
        platform=platform,
        track_ids=track_ids,
    )

    return {
        "status": "ok",
        "build_id": build_id,
        "playlist": playlist,
        "library_playlist": library_playlist,
        "library_playlist_cached": True,
        "platform": platform,
        "platform_playlist_id": str(platform_playlist_id),
    }


@router.post("/forge/builds/{build_id}/resync")
def forge_builds_resync(build_id: str):
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    if str(build.get("source") or "").strip().lower() != "sync":
        return _error(
            "Only sync builds can be re-synced.",
            status_code=400,
            code="FORGE_SYNC_RESYNC_INVALID_SOURCE",
        )

    summary = build.get("summary") if isinstance(build.get("summary"), dict) else {}
    source_url = str(summary.get("source_url") or "").strip()
    if not source_url:
        return _error(
            "Build summary is missing source_url; unable to re-sync.",
            status_code=400,
            code="FORGE_SYNC_RESYNC_MISSING_URL",
        )

    source = str(summary.get("source") or "").strip().lower() or _detect_sync_source(source_url)
    if source not in {"spotify", "lastfm", "deezer"}:
        return _error(
            "Unable to detect source from saved build summary.",
            status_code=400,
            code="FORGE_SYNC_UNSUPPORTED_SOURCE",
        )

    result = _import_sync_source(source, source_url)
    if result.get("status") != "ok":
        return _error(
            str(result.get("message") or "Sync re-load failed"),
            status_code=400,
            code="FORGE_SYNC_LOAD_FAILED",
        )

    shaped_tracks = [_shape_sync_track(t) for t in (result.get("tracks") or [])]
    total = int(result.get("track_count") or len(shaped_tracks))
    owned = int(result.get("owned_count") or sum(1 for t in shaped_tracks if t.get("is_owned")))
    missing = max(0, total - owned)

    updated_summary = dict(summary)
    updated_summary.update(
        {
            "source": source,
            "source_url": source_url,
            "track_count": total,
            "owned_count": owned,
            "missing_count": missing,
        }
    )

    updated = rythmx_store.update_forge_build(
        build_id,
        status="ready",
        run_mode="build",
        track_list=shaped_tracks,
        summary=updated_summary,
    )
    if not updated:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    return {
        "status": "ok",
        "build": updated,
        "source": source,
        "track_count": total,
        "owned_count": owned,
        "missing_count": missing,
    }


@router.post("/forge/builds/{build_id}/fetch")
def forge_builds_fetch(build_id: str):
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return _error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    fetch_enabled = _is_truthy(rythmx_store.get_setting("fetch_enabled", "false"))
    if not fetch_enabled:
        return _error(
            "Fetch is disabled in Settings.",
            status_code=400,
            code="FORGE_FETCH_DISABLED",
        )

    return _error(
        "Build fetch is planned but not implemented yet.",
        status_code=501,
        code="FORGE_FETCH_NOT_IMPLEMENTED",
    )
