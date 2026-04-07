"""
Forge Sync + Build routes.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Query

logger = logging.getLogger(__name__)
router = APIRouter()

_SYNC_BATCH_DEFAULT_CHUNK_SIZE = 500
_SYNC_BATCH_MIN_CHUNK_SIZE = 100
_SYNC_BATCH_MAX_CHUNK_SIZE = 2000
_SYNC_FIRST_N_DEFAULT = 500
_SYNC_FIRST_N_MAX = 10000
_SYNC_RESYNC_POLICIES = {"auto", "add_only", "replace"}
_SYNC_BATCH_JOBS: dict[str, dict[str, Any]] = {}
_SYNC_BATCH_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _clamp_chunk_size(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _SYNC_BATCH_DEFAULT_CHUNK_SIZE
    return max(_SYNC_BATCH_MIN_CHUNK_SIZE, min(value, _SYNC_BATCH_MAX_CHUNK_SIZE))


def _coerce_max_tracks(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 1:
        return None
    return min(value, _SYNC_FIRST_N_MAX)


def _track_signature(item: dict[str, Any]) -> str:
    track_id = str(item.get("track_id") or item.get("plex_rating_key") or item.get("navidrome_track_id") or "").strip()
    if track_id:
        return f"id:{track_id}"
    spotify_track_id = str(item.get("spotify_track_id") or "").strip()
    if spotify_track_id:
        return f"sp:{spotify_track_id}"
    artist = str(item.get("artist_name") or "").strip().lower()
    title = str(item.get("track_name") or "").strip().lower()
    album = str(item.get("album_name") or "").strip().lower()
    return f"text:{artist}|{title}|{album}"


def _merge_add_only(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    ordered_keys: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        safe = dict(item or {})
        key = _track_signature(safe)
        if key in by_key:
            continue
        ordered_keys.append(key)
        by_key[key] = safe

    added_count = 0
    for item in incoming:
        safe = dict(item or {})
        key = _track_signature(safe)
        if key in by_key:
            merged = dict(by_key[key])
            merged.update(safe)
            by_key[key] = merged
            continue
        ordered_keys.append(key)
        by_key[key] = safe
        added_count += 1

    merged_list = [by_key[k] for k in ordered_keys]
    return merged_list, added_count


def _diff_counts(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> tuple[int, int]:
    existing_keys = {_track_signature(dict(item or {})) for item in existing}
    incoming_keys = {_track_signature(dict(item or {})) for item in incoming}
    added = len(incoming_keys - existing_keys)
    removed = len(existing_keys - incoming_keys)
    return added, removed


def _default_resync_policy(*, batch_mode: bool, track_count: int, partial_load: bool = False) -> str:
    if batch_mode or partial_load or track_count > _SYNC_FIRST_N_DEFAULT:
        return "add_only"
    return "replace"


def _resolve_resync_policy(*, requested: str | None, summary: dict[str, Any], track_count: int) -> str:
    req = str(requested or "").strip().lower()
    if req in {"add_only", "replace"}:
        return req
    summary_policy = str(summary.get("resync_policy") or "").strip().lower()
    if summary_policy in {"add_only", "replace"}:
        return summary_policy
    summary_load_mode = str(summary.get("load_mode") or "").strip().lower()
    summary_partial = summary_load_mode == "first_n" or summary.get("applied_max_tracks") not in {None, "", 0}
    return _default_resync_policy(
        batch_mode=bool(summary.get("batch_mode")),
        track_count=track_count,
        partial_load=summary_partial,
    )


def _set_sync_job_state(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with _SYNC_BATCH_LOCK:
        current = _SYNC_BATCH_JOBS.get(job_id)
        if current is None:
            return None
        current.update(updates)
        current["updated_at"] = _utc_now_iso()
        _SYNC_BATCH_JOBS[job_id] = current
        return dict(current)


def _get_sync_job_state(job_id: str) -> dict[str, Any] | None:
    with _SYNC_BATCH_LOCK:
        current = _SYNC_BATCH_JOBS.get(job_id)
        return dict(current) if current is not None else None


def _run_sync_batch_job(
    *,
    job_id: str,
    source: str,
    source_url: str,
    chunk_size: int,
    build_id: str | None,
    max_tracks: int | None,
    resync_policy: str,
) -> None:
    from app.routes import forge as facade

    _set_sync_job_state(job_id, status="running", message="Importing source playlist")

    def _compact_build(build_value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(build_value, dict):
            return None
        return {
            "id": build_value.get("id"),
            "name": build_value.get("name"),
            "source": build_value.get("source"),
            "status": build_value.get("status"),
            "item_count": build_value.get("item_count"),
            "updated_at": build_value.get("updated_at"),
        }

    try:
        result = facade._import_sync_source(source, source_url)
        if result.get("status") != "ok":
            _set_sync_job_state(
                job_id,
                status="failed",
                error=str(result.get("message") or "Sync load failed"),
                message="Import failed",
                finished_at=_utc_now_iso(),
            )
            if build_id:
                facade.rythmx_store.update_forge_build(build_id, status="failed")
            return

        shaped_tracks = [facade._shape_sync_track(t) for t in (result.get("tracks") or [])]
        source_total = int(result.get("track_count") or len(shaped_tracks))
        if max_tracks and max_tracks > 0:
            shaped_tracks = shaped_tracks[:max_tracks]
        total = len(shaped_tracks)
        total_chunks = (total + chunk_size - 1) // chunk_size if total > 0 else 0

        processed_tracks = 0
        completed_chunks = 0
        owned_count = 0
        aggregate: list[dict[str, Any]] = []

        _set_sync_job_state(
            job_id,
            status="running",
            source_track_count=source_total,
            total_tracks=total,
            total_chunks=total_chunks,
            message=f"Processing {total_chunks} chunk(s)",
        )

        for i in range(0, len(shaped_tracks), chunk_size):
            chunk = shaped_tracks[i:i + chunk_size]
            aggregate.extend(chunk)
            processed_tracks += len(chunk)
            completed_chunks += 1
            owned_count += sum(1 for t in chunk if t.get("is_owned"))
            missing_count = max(0, processed_tracks - owned_count)

            if build_id:
                facade.rythmx_store.update_forge_build(
                    build_id,
                    status="building",
                    run_mode="build",
                    track_list=list(aggregate),
                    summary={
                        "source": source,
                        "source_url": source_url,
                        "batch_mode": True,
                        "load_mode": "batch",
                        "chunk_size": chunk_size,
                        "source_track_count": source_total,
                        "applied_max_tracks": max_tracks,
                        "resync_policy": resync_policy,
                        "total_chunks": total_chunks,
                        "completed_chunks": completed_chunks,
                        "track_count": processed_tracks,
                        "owned_count": owned_count,
                        "missing_count": missing_count,
                    },
                )

            _set_sync_job_state(
                job_id,
                status="running",
                processed_tracks=processed_tracks,
                completed_chunks=completed_chunks,
                owned_count=owned_count,
                missing_count=missing_count,
                message=f"Chunk {completed_chunks}/{total_chunks} complete",
            )

        final_missing = max(0, total - owned_count)
        build = None
        if build_id:
            build = facade.rythmx_store.update_forge_build(
                build_id,
                status="ready",
                run_mode="build",
                track_list=list(aggregate),
                summary={
                    "source": source,
                    "source_url": source_url,
                    "batch_mode": True,
                    "load_mode": "batch",
                    "chunk_size": chunk_size,
                    "source_track_count": source_total,
                    "applied_max_tracks": max_tracks,
                    "resync_policy": resync_policy,
                    "total_chunks": total_chunks,
                    "completed_chunks": completed_chunks,
                    "track_count": total,
                    "owned_count": owned_count,
                    "missing_count": final_missing,
                },
            )

        _set_sync_job_state(
            job_id,
            status="completed",
            source_track_count=source_total,
            total_tracks=total,
            processed_tracks=processed_tracks,
            total_chunks=total_chunks,
            completed_chunks=completed_chunks,
            owned_count=owned_count,
            missing_count=final_missing,
            message="Batch sync complete",
            build=_compact_build(build),
            finished_at=_utc_now_iso(),
        )
    except Exception as exc:
        logger.error("forge/sync/load batch job failed (%s): %s", job_id, exc, exc_info=True)
        _set_sync_job_state(
            job_id,
            status="failed",
            error=str(exc),
            message="Batch sync failed",
            finished_at=_utc_now_iso(),
        )
        if build_id:
            try:
                facade.rythmx_store.update_forge_build(build_id, status="failed")
            except Exception:
                pass


@router.post("/forge/sync/load")
def forge_sync_load(data: Optional[dict[str, Any]] = Body(default=None)):
    from app.routes import forge as facade

    payload = data if isinstance(data, dict) else {}
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url:
        return facade._error(
            "source_url is required",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )

    source = str(payload.get("source") or "").strip().lower() or facade._detect_sync_source(source_url)
    if source not in {"spotify", "lastfm", "deezer"}:
        return facade._error(
            "Unable to detect source. Supported URLs: Spotify, Last.fm, Deezer.",
            status_code=400,
            code="FORGE_SYNC_UNSUPPORTED_SOURCE",
        )

    batch_mode = bool(payload.get("batch_mode", False))
    queue_build = bool(payload.get("queue_build", True))
    chunk_size = _clamp_chunk_size(payload.get("chunk_size"))
    max_tracks = _coerce_max_tracks(payload.get("max_tracks"))
    if payload.get("max_tracks") is not None and max_tracks is None:
        return facade._error(
            f"max_tracks must be an integer between 1 and {_SYNC_FIRST_N_MAX}",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )
    default_policy = _default_resync_policy(
        batch_mode=batch_mode,
        track_count=max_tracks or (_SYNC_FIRST_N_DEFAULT + 1 if batch_mode else _SYNC_FIRST_N_DEFAULT),
        partial_load=bool(max_tracks),
    )

    if batch_mode:
        explicit_name = str(payload.get("name") or "").strip()
        build_name = explicit_name or f"Sync {source.title()} {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        build = None
        build_id = None
        if queue_build:
            build = facade.rythmx_store.create_forge_build(
                name=build_name,
                source="sync",
                status="building",
                run_mode="build",
                track_list=[],
                summary={
                    "source": source,
                    "source_url": source_url,
                    "batch_mode": True,
                    "load_mode": "batch",
                    "chunk_size": chunk_size,
                    "source_track_count": 0,
                    "applied_max_tracks": max_tracks,
                    "resync_policy": default_policy,
                    "track_count": 0,
                    "owned_count": 0,
                    "missing_count": 0,
                    "total_chunks": 0,
                    "completed_chunks": 0,
                },
            )
            build_id = str(build.get("id") or "")

        job_id = str(uuid.uuid4())
        with _SYNC_BATCH_LOCK:
            _SYNC_BATCH_JOBS[job_id] = {
                "job_id": job_id,
                "mode": "batch",
                "status": "queued",
                "source": source,
                "source_url": source_url,
                "queue_build": queue_build,
                "chunk_size": chunk_size,
                "max_tracks": max_tracks,
                "resync_policy": default_policy,
                "build_id": build_id,
                "build": build,
                "total_tracks": 0,
                "processed_tracks": 0,
                "total_chunks": 0,
                "completed_chunks": 0,
                "owned_count": 0,
                "missing_count": 0,
                "message": "Queued",
                "error": None,
                "started_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "finished_at": None,
            }

        thread = threading.Thread(
            target=_run_sync_batch_job,
            kwargs={
                "job_id": job_id,
                "source": source,
                "source_url": source_url,
                "chunk_size": chunk_size,
                "build_id": build_id,
                "max_tracks": max_tracks,
                "resync_policy": default_policy,
            },
            daemon=True,
            name=f"forge-sync-batch-{job_id[:8]}",
        )
        thread.start()

        return {
            "status": "ok",
            "mode": "batch",
            "job_id": job_id,
            "source": source,
            "name": build_name,
            "track_count": 0,
            "owned_count": 0,
            "missing_count": 0,
            "queue_build": queue_build,
            "chunk_size": chunk_size,
            "source_track_count": 0,
            "applied_max_tracks": max_tracks,
            "resync_policy": default_policy,
            "build": build,
            "tracks": [],
        }

    result = facade._import_sync_source(source, source_url)
    if result.get("status") != "ok":
        return facade._error(
            str(result.get("message") or "Sync load failed"),
            status_code=400,
            code="FORGE_SYNC_LOAD_FAILED",
        )

    shaped_tracks = [facade._shape_sync_track(t) for t in (result.get("tracks") or [])]
    source_total = int(result.get("track_count") or len(shaped_tracks))
    if max_tracks and max_tracks > 0:
        shaped_tracks = shaped_tracks[:max_tracks]
    total = len(shaped_tracks)
    owned = int(sum(1 for t in shaped_tracks if t.get("is_owned")))
    missing = max(0, total - owned)
    partial_load = bool(max_tracks and source_total > total)
    resolved_policy = _default_resync_policy(
        batch_mode=False,
        track_count=source_total,
        partial_load=partial_load,
    )

    build = None
    if queue_build:
        explicit_name = str(payload.get("name") or "").strip()
        default_name = str(result.get("name") or f"Sync {source.title()}").strip()
        build_name = explicit_name or default_name
        build = facade.rythmx_store.create_forge_build(
            name=build_name,
            source="sync",
            status="ready",
            run_mode="build",
            track_list=shaped_tracks,
            summary={
                "source": source,
                "source_url": source_url,
                "batch_mode": False,
                "load_mode": "first_n" if max_tracks else "all",
                "source_track_count": source_total,
                "applied_max_tracks": max_tracks,
                "resync_policy": resolved_policy,
                "track_count": total,
                "owned_count": owned,
                "missing_count": missing,
            },
        )

    return {
        "status": "ok",
        "mode": "immediate",
        "source": source,
        "name": result.get("name"),
        "track_count": total,
        "owned_count": owned,
        "missing_count": missing,
        "source_track_count": source_total,
        "applied_max_tracks": max_tracks,
        "resync_policy": resolved_policy,
        "queue_build": queue_build,
        "build": build,
        "tracks": shaped_tracks,
    }


@router.get("/forge/sync/jobs/{job_id}")
def forge_sync_job_get(job_id: str):
    from app.routes import forge as facade

    job = _get_sync_job_state(str(job_id or "").strip())
    if not job:
        return facade._error(
            "Sync batch job not found",
            status_code=404,
            code="FORGE_SYNC_JOB_NOT_FOUND",
        )
    return {"status": "ok", "job": job}


@router.get("/forge/builds")
def forge_builds_list(
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    from app.routes import forge as facade

    if source and source not in facade._BUILD_SOURCES:
        return facade._error(
            f"source must be one of: {', '.join(sorted(facade._BUILD_SOURCES))}",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )
    builds = facade.rythmx_store.list_forge_builds(source=source, limit=limit)
    return {"status": "ok", "builds": builds}


@router.get("/forge/builds/{build_id}")
def forge_builds_get(build_id: str):
    from app.routes import forge as facade

    build = facade.rythmx_store.get_forge_build(build_id)
    if not build:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "build": build}


@router.patch("/forge/builds/{build_id}")
def forge_builds_update(build_id: str, data: Optional[dict[str, Any]] = Body(default=None)):
    from app.routes import forge as facade

    payload = data or {}
    validation_error = facade._validate_build_update_payload(payload)
    if validation_error:
        return facade._error(validation_error, status_code=400, code="FORGE_VALIDATION_ERROR")

    updated = facade.rythmx_store.update_forge_build(
        build_id,
        name=payload.get("name") if "name" in payload else None,
        status=payload.get("status") if "status" in payload else None,
        run_mode=payload.get("run_mode") if "run_mode" in payload else None,
        track_list=payload.get("track_list") if "track_list" in payload else None,
        summary=payload.get("summary") if "summary" in payload else None,
    )
    if not updated:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "build": updated}


@router.post("/forge/builds")
def forge_builds_create(data: Optional[dict[str, Any]] = Body(default=None)):
    from app.routes import forge as facade

    data = data or {}
    validation_error = facade._validate_build_payload(data)
    if validation_error:
        return facade._error(validation_error, status_code=400, code="FORGE_VALIDATION_ERROR")

    source = str(data.get("source", "manual")).strip().lower()
    status = str(data.get("status", "ready")).strip().lower()
    run_mode = str(data.get("run_mode")).strip().lower() if data.get("run_mode") else None
    track_list = data.get("track_list") or []
    summary = data.get("summary") or {}

    default_name = f"{source.replace('_', ' ').title()} Build {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    name = str(data.get("name") or "").strip() or default_name

    build = facade.rythmx_store.create_forge_build(
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
    from app.routes import forge as facade

    deleted = facade.rythmx_store.delete_forge_build(build_id)
    if not deleted:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")
    return {"status": "ok", "deleted": True}


@router.post("/forge/builds/{build_id}/publish")
def forge_builds_publish(build_id: str, data: Optional[dict[str, Any]] = Body(default=None)):
    from app.routes import forge as facade

    build = facade.rythmx_store.get_forge_build(build_id)
    if not build:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    platform = facade._get_library_platform()
    if platform == "jellyfin":
        return facade._error(
            "Jellyfin publish is planned but not implemented yet.",
            status_code=501,
            code="FORGE_PUBLISH_NOT_IMPLEMENTED",
        )

    request_data = data or {}
    playlist_name = str(request_data.get("name") or build.get("name") or "").strip()
    if not playlist_name:
        playlist_name = f"Build {build_id}"

    track_ids = facade._extract_publish_track_ids(build.get("track_list") or [])
    if not track_ids:
        return facade._error(
            "Build has no publishable library track IDs (track_id / plex_rating_key / navidrome_track_id).",
            status_code=400,
            code="FORGE_PUBLISH_EMPTY",
        )

    pusher = facade.get_playlist_pusher()
    try:
        platform_playlist_id = facade._push_playlist(pusher, playlist_name, track_ids)
    except Exception as exc:
        logger.error("forge/builds/%s/publish: pusher error: %s", build_id, exc, exc_info=True)
        return facade._error(str(exc), status_code=500, code="FORGE_PUBLISH_FAILED")

    if not platform_playlist_id:
        return facade._error(
            "Platform publish failed; check platform credentials/logs.",
            status_code=502,
            code="FORGE_PUBLISH_FAILED",
        )

    pushed_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    playlist = facade.rythmx_store.upsert_forge_playlist(
        playlist_id=build_id,
        name=playlist_name,
        track_ids=track_ids,
        pushed_at=pushed_at,
    )
    facade.rythmx_store.update_forge_build_status(build_id, "published")

    library_playlist = facade._sync_library_playlist_cache(
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
def forge_builds_resync(build_id: str, data: Optional[dict[str, Any]] = Body(default=None)):
    from app.routes import forge as facade

    payload = data if isinstance(data, dict) else {}
    requested_policy = str(payload.get("resync_policy") or "").strip().lower()
    if requested_policy and requested_policy not in _SYNC_RESYNC_POLICIES:
        return facade._error(
            f"resync_policy must be one of: {', '.join(sorted(_SYNC_RESYNC_POLICIES))}",
            status_code=400,
            code="FORGE_VALIDATION_ERROR",
        )

    build = facade.rythmx_store.get_forge_build(build_id)
    if not build:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    if str(build.get("source") or "").strip().lower() != "sync":
        return facade._error(
            "Only sync builds can be re-synced.",
            status_code=400,
            code="FORGE_SYNC_RESYNC_INVALID_SOURCE",
        )

    summary = build.get("summary") if isinstance(build.get("summary"), dict) else {}
    source_url = str(summary.get("source_url") or "").strip()
    if not source_url:
        return facade._error(
            "Build summary is missing source_url; unable to re-sync.",
            status_code=400,
            code="FORGE_SYNC_RESYNC_MISSING_URL",
        )

    source = str(summary.get("source") or "").strip().lower() or facade._detect_sync_source(source_url)
    if source not in {"spotify", "lastfm", "deezer"}:
        return facade._error(
            "Unable to detect source from saved build summary.",
            status_code=400,
            code="FORGE_SYNC_UNSUPPORTED_SOURCE",
        )

    result = facade._import_sync_source(source, source_url)
    if result.get("status") != "ok":
        return facade._error(
            str(result.get("message") or "Sync re-load failed"),
            status_code=400,
            code="FORGE_SYNC_LOAD_FAILED",
        )

    shaped_tracks = [facade._shape_sync_track(t) for t in (result.get("tracks") or [])]
    source_total = int(result.get("track_count") or len(shaped_tracks))

    existing_tracks = [dict(item or {}) for item in (build.get("track_list") or []) if isinstance(item, dict)]
    resync_policy = _resolve_resync_policy(
        requested=requested_policy if requested_policy != "auto" else None,
        summary=summary,
        track_count=source_total,
    )
    if resync_policy == "add_only":
        final_tracks, added_count = _merge_add_only(existing_tracks, shaped_tracks)
        removed_count = 0
    else:
        final_tracks = list(shaped_tracks)
        added_count, removed_count = _diff_counts(existing_tracks, shaped_tracks)

    total = len(final_tracks)
    owned = int(sum(1 for t in final_tracks if t.get("is_owned")))
    missing = max(0, total - owned)

    updated_summary = dict(summary)
    updated_summary.update(
        {
            "source": source,
            "source_url": source_url,
            "source_track_count": source_total,
            "track_count": total,
            "owned_count": owned,
            "missing_count": missing,
            "resync_policy": resync_policy,
            "last_resync_policy": resync_policy,
            "last_resync_added": added_count,
            "last_resync_removed": removed_count,
        }
    )

    updated = facade.rythmx_store.update_forge_build(
        build_id,
        status="ready",
        run_mode="build",
        track_list=final_tracks,
        summary=updated_summary,
    )
    if not updated:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    return {
        "status": "ok",
        "build": updated,
        "source": source,
        "resync_policy": resync_policy,
        "added_count": added_count,
        "removed_count": removed_count,
        "track_count": total,
        "owned_count": owned,
        "missing_count": missing,
    }


@router.post("/forge/builds/{build_id}/fetch")
def forge_builds_fetch(build_id: str):
    from app.routes import forge as facade
    from app.plugins import get_downloader
    from app.db import rythmx_store as _store

    build = facade.rythmx_store.get_forge_build(build_id)
    if not build:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    fetch_enabled = facade._is_truthy(facade.rythmx_store.get_setting("fetch_enabled", "false"))
    if not fetch_enabled:
        return facade._error(
            "Fetch is disabled in Settings.",
            status_code=400,
            code="FORGE_FETCH_DISABLED",
        )

    downloader = get_downloader()
    if getattr(downloader, "name", "stub") == "stub":
        return facade._error(
            "No downloader plugin configured. Install a plugin and enable it in Settings → Integrations.",
            status_code=400,
            code="FORGE_NO_DOWNLOADER",
        )

    track_list = build.get("track_list") or []

    # Deduplicate by (artist_name, album_name) — one submission per unique missing album.
    seen: set[tuple[str, str]] = set()
    albums_to_fetch: list[dict] = []
    for item in track_list:
        if not isinstance(item, dict):
            continue
        if bool(item.get("is_owned", False)):
            continue  # skip already-owned tracks
        artist = str(item.get("artist_name") or "").strip()
        album = str(item.get("album_name") or "").strip()
        if not artist or not album:
            continue
        key = (artist.lower(), album.lower())
        if key in seen:
            continue
        seen.add(key)
        albums_to_fetch.append(item)

    if not albums_to_fetch:
        return {
            "status": "ok",
            "build_id": build_id,
            "message": "No missing albums to fetch.",
            "submitted": 0,
            "skipped": 0,
            "jobs": [],
        }

    submitted = 0
    skipped = 0
    jobs = []
    for item in albums_to_fetch:
        artist = str(item.get("artist_name") or "").strip()
        album = str(item.get("album_name") or "").strip()
        metadata = {
            "deezer_id": item.get("deezer_album_id"),
            "itunes_id": item.get("itunes_album_id"),
            "release_date": item.get("release_date"),
            "thumb_url": item.get("thumb_url"),
        }
        try:
            job_id = downloader.submit(artist, album, metadata)
        except Exception as exc:
            logger.warning(
                "forge/fetch: downloader submit failed for '%s — %s': %s",
                artist, album, exc,
            )
            skipped += 1
            continue

        if job_id.startswith("unresolved:"):
            logger.warning(
                "forge/fetch: downloader could not resolve '%s — %s': %s",
                artist, album, job_id,
            )
            skipped += 1
            continue

        _store.insert_download_job(
            build_id=build_id,
            job_id=job_id,
            provider=downloader.name,
            artist_name=artist,
            album_name=album,
        )
        submitted += 1
        jobs.append({"job_id": job_id, "artist": artist, "album": album})

    return {
        "status": "ok",
        "build_id": build_id,
        "submitted": submitted,
        "skipped": skipped,
        "jobs": jobs,
    }


@router.get("/forge/builds/{build_id}/fetch/status")
def forge_builds_fetch_status(build_id: str):
    from app.routes import forge as facade
    from app.db import rythmx_store as _store

    build = facade.rythmx_store.get_forge_build(build_id)
    if not build:
        return facade._error("Build not found", status_code=404, code="FORGE_BUILD_NOT_FOUND")

    jobs = _store.get_download_jobs_for_build(build_id)
    pending = sum(1 for j in jobs if j["status"] == "pending")
    completed = sum(1 for j in jobs if j["status"] == "completed")
    failed = sum(1 for j in jobs if j["status"] == "failed")

    return {
        "status": "ok",
        "build_id": build_id,
        "total": len(jobs),
        "pending": pending,
        "completed": completed,
        "failed": failed,
        "jobs": jobs,
    }

