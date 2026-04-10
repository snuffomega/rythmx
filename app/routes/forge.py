"""
forge.py - Forge route facade and shared helpers.

Router is registered at /api/v1 in main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import config
from app.db import get_playlist_pusher
from app.db import rythmx_store
from app.db.sql_helpers import build_in_clause
from app.dependencies import verify_api_key
from app.services import playlist_importer

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
            rows = conn.execute(
                "SELECT id, duration FROM lib_tracks WHERE id IN " + build_in_clause(len(ordered_ids)),
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


def _get_discovered_releases() -> list[dict]:
    """
    Query forge_discovered_releases JOIN forge_discovered_artists.
    in_library is release-level: true only when this specific release is owned in lib_releases.
    Returns list of release dicts.
    """
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.artist_deezer_id,
                da.name        AS artist_name,
                la.id          AS library_artist_id,
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
                AND lr.is_owned = 1
            ORDER BY r.release_date DESC, da.name ASC
            LIMIT 500
            """
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Route module includes + compatibility exports
# ---------------------------------------------------------------------------
from app.routes.forge_pipeline_history_routes import get_pipeline_history, router as _pipeline_history_router
from app.routes.forge_new_music_routes import (
    nm_clear,
    nm_get_config,
    nm_get_release_tracks,
    nm_get_results,
    nm_run,
    nm_save_config,
    router as _new_music_router,
)
from app.routes.forge_discovery_routes import (
    discovery_get_config,
    discovery_get_results,
    discovery_run,
    discovery_save_config,
    router as _discovery_router,
)
from app.routes.forge_sync_build_routes import (
    forge_builds_create,
    forge_builds_delete,
    forge_builds_fetch,
    forge_builds_fetch_status,
    forge_builds_get,
    forge_builds_list,
    forge_builds_publish,
    forge_builds_resync,
    forge_builds_update,
    forge_fetch_run_get,
    forge_fetch_run_retry,
    forge_fetch_run_tasks,
    forge_fetch_queue_cancel_batch,
    forge_fetch_queue_cancel_item,
    forge_fetch_queue_enqueue,
    forge_fetch_queue_list,
    forge_fetch_runs_list,
    forge_sync_job_get,
    forge_sync_load,
    router as _sync_build_router,
)

router.include_router(_pipeline_history_router)
router.include_router(_new_music_router)
router.include_router(_discovery_router)
router.include_router(_sync_build_router)
