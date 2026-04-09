"""
fetch_pipeline.py - Provider-agnostic fetch control plane (Tidarr first).

Owns run/task orchestration for Forge fetch flows:
  - run persistence (`fetch_runs`)
  - task persistence (`fetch_tasks`)
  - provider submission + polling
  - post-download plugin pipeline (tagger/file_handler)
  - stage-1 library sync + ownership confirmation
"""
from __future__ import annotations

import glob as _glob
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from app import plugins as _plugins
from app.db import get_library_reader
from app.db import rythmx_store
from app.plugins import DownloadArtifact
from app.routes.ws import broadcast

logger = logging.getLogger(__name__)

FETCH_STAGES = (
    "queued",
    "submitted",
    "downloading",
    "downloaded",
    "tagged",
    "moved",
    "scan_requested",
    "in_library",
    "failed",
    "unresolved",
)

_TERMINAL_STAGES = frozenset({"in_library", "failed", "unresolved"})
_SCAN_LOCK = threading.Lock()


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_json(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, type(fallback)):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except Exception:
        return fallback


def _emit_progress(*, run_id: str, stage: str, processed: int, total: int, message: str) -> None:
    broadcast(
        "pipeline_progress",
        {
            "pipeline": "fetch",
            "run_id": run_id,
            "stage": stage,
            "processed": int(processed),
            "total": int(total),
            "message": message,
        },
    )


def _emit_complete(*, run_id: str, summary: dict[str, Any]) -> None:
    broadcast(
        "pipeline_complete",
        {
            "pipeline": "fetch",
            "run_id": run_id,
            "summary": summary,
        },
    )


def _emit_error(*, run_id: str, message: str) -> None:
    broadcast(
        "pipeline_error",
        {
            "pipeline": "fetch",
            "run_id": run_id,
            "message": str(message or "Fetch pipeline failed"),
        },
    )


def _build_fetch_candidates(build: dict[str, Any]) -> list[dict[str, Any]]:
    track_list = build.get("track_list") or []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(track_list):
        if not isinstance(item, dict):
            continue
        if bool(item.get("is_owned", False)) or bool(item.get("in_library", 0)):
            continue

        artist = str(item.get("artist_name") or "").strip()
        album = str(item.get("album_name") or item.get("title") or "").strip()
        if not artist or not album:
            continue

        key = (artist.lower(), album.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "artist_name": artist,
                "album_name": album,
                "artist_key": key[0],
                "album_key": key[1],
                "metadata": {
                    "deezer_album_id": item.get("deezer_album_id") or item.get("deezer_id"),
                    "itunes_album_id": item.get("itunes_album_id"),
                    "release_date": item.get("release_date"),
                    "thumb_url": item.get("thumb_url") or item.get("cover_url"),
                    "item_index": idx,
                },
            }
        )
    return out


def _fetch_wait_timeout_s() -> int:
    raw = rythmx_store.get_setting("fd_fetch_wait_timeout_s", "600")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 600
    return max(30, min(value, 7200))


def _classify_error(exc: Exception) -> tuple[str, str, str]:
    et = str(getattr(exc, "error_type", "")).strip().lower()
    if et in {"recoverable", "permanent", "config"}:
        error_type = et
    elif isinstance(exc, (TimeoutError, ConnectionError)):
        error_type = "recoverable"
    elif isinstance(exc, ValueError):
        error_type = "config"
    else:
        error_type = "permanent"
    code = str(getattr(exc, "error_code", "")).strip() or exc.__class__.__name__
    message = str(exc) or "Unknown fetch error"
    return error_type, code, message


def _task_from_row(row: dict[str, Any]) -> dict[str, Any]:
    task = dict(row)
    task["metadata"] = _safe_json(task.get("metadata_json"), {})
    return task


def _set_task_fields(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    now = _utcnow()
    updates = []
    params: list[Any] = []
    for key, value in fields.items():
        updates.append(f"{key} = ?")
        params.append(value)
    updates.extend(["updated_at = ?", "last_transition_at = ?"])
    params.extend([now, now, task_id])

    with rythmx_store._connect() as conn:
        conn.execute(
            f"UPDATE fetch_tasks SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )


def _set_task_stage(
    task_id: int,
    stage: str,
    *,
    error_type: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    provider_job_id: str | None = None,
    storage_path: str | None = None,
    source_dir: str | None = None,
    dest_dir: str | None = None,
    scan_deadline_at: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "stage": stage,
        "error_type": error_type,
        "error_code": error_code,
        "error_message": error_message,
    }
    if provider_job_id is not None:
        payload["provider_job_id"] = provider_job_id
    if storage_path is not None:
        payload["storage_path"] = storage_path
    if source_dir is not None:
        payload["source_dir"] = source_dir
    if dest_dir is not None:
        payload["dest_dir"] = dest_dir
    if scan_deadline_at is not None:
        payload["scan_deadline_at"] = scan_deadline_at
    if stage in _TERMINAL_STAGES:
        payload["completed_at"] = _utcnow()
    _set_task_fields(task_id, **payload)


def _list_tasks(
    *,
    run_id: str | None = None,
    stages: tuple[str, ...] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where: list[str] = []

    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if stages:
        where.append("stage IN (" + ",".join(["?"] * len(stages)) + ")")
        params.extend(stages)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM fetch_tasks
            {where_sql}
            ORDER BY created_at ASC
            LIMIT ?
            """,
            tuple(params + [limit]),
        ).fetchall()
    return [_task_from_row(dict(r)) for r in rows]


def _task_stage_counts(run_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT stage, COUNT(*) AS cnt
            FROM fetch_tasks
            WHERE run_id = ?
            GROUP BY stage
            """,
            (run_id,),
        ).fetchall()
    for row in rows:
        counts[str(row["stage"])] = int(row["cnt"] or 0)
    return counts


def _run_summary(run_id: str) -> dict[str, Any]:
    with rythmx_store._connect() as conn:
        run_row = conn.execute(
            """
            SELECT fr.*, fb.source AS build_source, fb.name AS build_name, fb.status AS build_status
            FROM fetch_runs fr
            LEFT JOIN forge_builds fb ON fb.id = fr.build_id
            WHERE fr.id = ?
            """,
            (run_id,),
        ).fetchone()
    if not run_row:
        return {}

    run = dict(run_row)
    counts = _task_stage_counts(run_id)
    total = sum(counts.values())
    terminal = sum(v for k, v in counts.items() if k in _TERMINAL_STAGES)
    run["stage_counts"] = counts
    run["total_tasks"] = total
    run["processed_tasks"] = terminal
    run["active_tasks"] = max(0, total - terminal)
    run["in_library"] = counts.get("in_library", 0)
    run["failed"] = counts.get("failed", 0)
    run["unresolved"] = counts.get("unresolved", 0)
    run["config"] = _safe_json(run.get("config_json"), {})
    return run


def _reconcile_run(run_id: str) -> dict[str, Any]:
    summary = _run_summary(run_id)
    if not summary:
        return {}

    total = int(summary.get("total_tasks", 0))
    failed = int(summary.get("failed", 0))
    unresolved = int(summary.get("unresolved", 0))
    active = int(summary.get("active_tasks", 0))
    in_library = int(summary.get("in_library", 0))

    if total == 0:
        next_status = "completed"
    elif active > 0:
        next_status = "running"
    elif failed + unresolved >= total:
        next_status = "failed"
    else:
        next_status = "completed"

    now = _utcnow()
    run_id_val = str(summary["id"])
    status_changed = str(summary.get("status") or "") != next_status

    with rythmx_store._connect() as conn:
        conn.execute(
            """
            UPDATE fetch_runs
            SET status = ?, total_tasks = ?, updated_at = ?,
                finished_at = CASE WHEN ? IN ('completed', 'failed') THEN COALESCE(finished_at, ?) ELSE NULL END,
                last_error = CASE WHEN ? = 'failed' THEN COALESCE(last_error, 'Fetch run ended with unresolved failures') ELSE NULL END
            WHERE id = ?
            """,
            (next_status, total, now, next_status, now, next_status, run_id_val),
        )

    refreshed = _run_summary(run_id_val)

    if status_changed and next_status == "completed":
        _emit_complete(
            run_id=run_id_val,
            summary={
                "status": next_status,
                "in_library": in_library,
                "failed": failed,
                "unresolved": unresolved,
                "total": total,
            },
        )
    elif status_changed and next_status == "failed":
        _emit_error(
            run_id=run_id_val,
            message=f"Fetch run failed ({failed} failed, {unresolved} unresolved).",
        )
    else:
        _emit_progress(
            run_id=run_id_val,
            stage="running" if next_status == "running" else next_status,
            processed=refreshed.get("processed_tasks", 0),
            total=total,
            message=f"{refreshed.get('in_library', 0)} in library, {failed} failed, {unresolved} unresolved",
        )

    return refreshed


def _mark_build_item_in_library(build_id: str, artist_name: str, album_name: str) -> None:
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return

    track_list = list(build.get("track_list") or [])
    changed = False
    for item in track_list:
        if not isinstance(item, dict):
            continue
        artist = str(item.get("artist_name") or "").strip().lower()
        album = str(item.get("album_name") or item.get("title") or "").strip().lower()
        if artist == artist_name.lower() and album == album_name.lower():
            if not bool(item.get("is_owned", False)):
                item["is_owned"] = True
                changed = True
            if not bool(item.get("in_library", 0)):
                item["in_library"] = 1
                changed = True

    if not changed:
        return

    summary = dict(build.get("summary") or {})
    source = str(build.get("source") or "").strip().lower()
    if source == "sync":
        owned_count = int(sum(1 for t in track_list if bool(t.get("is_owned", False))))
        total = len(track_list)
        summary["track_count"] = total
        summary["owned_count"] = owned_count
        summary["missing_count"] = max(0, total - owned_count)
    elif source == "new_music":
        in_lib = int(sum(1 for t in track_list if bool(t.get("in_library", 0))))
        summary["in_library_count"] = in_lib

    rythmx_store.update_forge_build(build_id, track_list=track_list, summary=summary)


def _run_stage1_sync_once() -> bool:
    """
    Trigger stage-1 sync only (no full enrichment DAG).
    Serialized to avoid overlapping sync runs from poll loop ticks.
    """
    acquired = _SCAN_LOCK.acquire(blocking=False)
    if not acquired:
        return False
    try:
        from app.services.enrichment.sync import sync_library

        result = sync_library()
        logger.info(
            "fetch_pipeline: stage-1 sync complete (artists=%s albums=%s tracks=%s)",
            result.get("artist_count", 0),
            result.get("album_count", 0),
            result.get("track_count", 0),
        )
        return True
    except Exception as exc:
        logger.warning("fetch_pipeline: stage-1 sync failed: %s", exc)
        return False
    finally:
        _SCAN_LOCK.release()


def _confirm_task_in_library(task: dict[str, Any]) -> bool:
    reader = get_library_reader()
    metadata = task.get("metadata") or {}
    match = reader.check_album_owned(
        task.get("artist_name") or "",
        task.get("album_name") or "",
        deezer_album_id=metadata.get("deezer_album_id"),
        itunes_album_id=metadata.get("itunes_album_id"),
    )
    if not match:
        return False

    _set_task_stage(int(task["id"]), "in_library")
    _mark_build_item_in_library(
        str(task.get("build_id") or ""),
        str(task.get("artist_name") or ""),
        str(task.get("album_name") or ""),
    )
    return True


def _submit_queued_tasks(*, run_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    tasks = _list_tasks(run_id=run_id, stages=("queued",), limit=limit)
    if not tasks:
        return {"submitted": 0, "unresolved": 0, "failed": 0, "jobs": []}

    downloader = _plugins.get_downloader()
    submitted = 0
    unresolved = 0
    failed = 0
    jobs: list[dict[str, str]] = []

    for task in tasks:
        task_id = int(task["id"])
        artist = str(task.get("artist_name") or "")
        album = str(task.get("album_name") or "")
        metadata = task.get("metadata") or {}

        try:
            job_id = str(downloader.submit(artist, album, metadata))
        except Exception as exc:
            etype, ecode, emsg = _classify_error(exc)
            _set_task_stage(
                task_id,
                "failed",
                error_type=etype,
                error_code=ecode,
                error_message=emsg,
            )
            failed += 1
            continue

        if job_id.startswith("unresolved:"):
            _set_task_stage(
                task_id,
                "unresolved",
                error_type="permanent",
                error_code="unresolved",
                error_message=job_id,
            )
            unresolved += 1
            continue

        _set_task_stage(
            task_id,
            "submitted",
            provider_job_id=job_id,
            error_type=None,
            error_code=None,
            error_message=None,
        )
        rythmx_store.insert_download_job(
            build_id=str(task.get("build_id") or ""),
            job_id=job_id,
            provider=str(task.get("provider") or getattr(downloader, "name", "unknown")),
            artist_name=artist,
            album_name=album,
        )
        submitted += 1
        jobs.append({"task_id": str(task_id), "job_id": job_id, "artist": artist, "album": album})

    return {"submitted": submitted, "unresolved": unresolved, "failed": failed, "jobs": jobs}


def _poll_provider_updates(limit: int = 500) -> dict[str, int]:
    tasks = _list_tasks(stages=("submitted", "downloading"), limit=limit)
    if not tasks:
        return {"downloaded": 0, "downloading": 0, "failed": 0}

    downloader = _plugins.get_downloader()
    history_slots = []
    queue_slots = []
    if hasattr(downloader, "poll_history"):
        try:
            history_slots = downloader.poll_history(limit=400) or []
        except Exception as exc:
            logger.warning("fetch_pipeline: poll_history failed: %s", exc)
    if hasattr(downloader, "poll_queue"):
        try:
            queue_slots = downloader.poll_queue() or []
        except Exception as exc:
            logger.warning("fetch_pipeline: poll_queue failed: %s", exc)

    history_by_id = {str(s.get("nzo_id") or ""): s for s in history_slots if s.get("nzo_id")}
    queue_ids = {str(s.get("nzo_id") or "") for s in queue_slots if s.get("nzo_id")}
    result = {"downloaded": 0, "downloading": 0, "failed": 0}

    for task in tasks:
        task_id = int(task["id"])
        job_id = str(task.get("provider_job_id") or "")
        if not job_id:
            continue

        slot = history_by_id.get(job_id)
        if slot:
            status = str(slot.get("status") or "").lower()
            if status == "completed":
                storage = str(slot.get("storage") or task.get("storage_path") or "")
                _set_task_stage(task_id, "downloaded", storage_path=storage)
                rythmx_store.update_download_job_status(job_id, "completed", storage_path=storage or None)
                result["downloaded"] += 1
                continue
            if status == "failed":
                _set_task_stage(
                    task_id,
                    "failed",
                    error_type="permanent",
                    error_code="provider_failed",
                    error_message=f"Provider marked job '{job_id}' as failed",
                )
                rythmx_store.update_download_job_status(job_id, "failed")
                result["failed"] += 1
                continue

        if job_id in queue_ids and task.get("stage") != "downloading":
            _set_task_stage(task_id, "downloading")
            result["downloading"] += 1

    return result


def _process_downloaded_tasks(limit: int = 200) -> dict[str, int]:
    tasks = _list_tasks(stages=("downloaded", "tagged"), limit=limit)
    if not tasks:
        return {"tagged": 0, "moved": 0, "failed": 0}

    downloader = _plugins.get_downloader()
    tagger = _plugins.get_tagger()
    file_handler = _plugins.get_file_handler()
    result = {"tagged": 0, "moved": 0, "failed": 0}

    for task in tasks:
        task_id = int(task["id"])
        storage_path = str(task.get("storage_path") or "")
        translate = getattr(downloader, "translate_path", lambda p: p)
        local_path = str(translate(storage_path))

        if not os.path.isdir(local_path):
            _set_task_stage(
                task_id,
                "failed",
                error_type="recoverable",
                error_code="source_not_accessible",
                error_message=f"Source path not accessible: {local_path}",
                source_dir=local_path,
            )
            result["failed"] += 1
            continue

        flac_files = sorted(_glob.glob(os.path.join(local_path, "**", "*.flac"), recursive=True))
        if not flac_files:
            _set_task_stage(
                task_id,
                "failed",
                error_type="recoverable",
                error_code="no_audio_files",
                error_message=f"No FLAC files found in: {local_path}",
                source_dir=local_path,
            )
            result["failed"] += 1
            continue

        artifact = DownloadArtifact(
            job_id=str(task.get("provider_job_id") or task.get("id")),
            artist=str(task.get("artist_name") or ""),
            album=str(task.get("album_name") or ""),
            source_dir=local_path,
            files=flac_files,
            metadata=dict(task.get("metadata") or {}),
        )

        if task.get("stage") == "downloaded":
            if tagger.name != "noop":
                try:
                    artifact = tagger.tag(artifact)
                except Exception as exc:
                    etype, ecode, emsg = _classify_error(exc)
                    _set_task_stage(
                        task_id,
                        "failed",
                        error_type=etype,
                        error_code=f"tagger:{ecode}",
                        error_message=emsg,
                        source_dir=local_path,
                    )
                    result["failed"] += 1
                    continue
            _set_task_stage(task_id, "tagged", source_dir=local_path)
            result["tagged"] += 1

        if file_handler.name != "noop":
            try:
                artifact = file_handler.organize(artifact)
            except Exception as exc:
                etype, ecode, emsg = _classify_error(exc)
                _set_task_stage(
                    task_id,
                    "failed",
                    error_type=etype,
                    error_code=f"file_handler:{ecode}",
                    error_message=emsg,
                    source_dir=local_path,
                )
                result["failed"] += 1
                continue

        dest = str(artifact.dest_dir or local_path)
        _set_task_stage(
            task_id,
            "moved",
            source_dir=local_path,
            dest_dir=dest,
            storage_path=dest,
        )
        if task.get("provider_job_id"):
            rythmx_store.update_download_job_status(str(task["provider_job_id"]), "completed", storage_path=dest)
        result["moved"] += 1

    return result


def _process_scan_requests(limit: int = 200) -> dict[str, int]:
    moved = _list_tasks(stages=("moved",), limit=limit)
    if moved:
        timeout_s = _fetch_wait_timeout_s()
        for task in moved:
            task_id = int(task["id"])
            timeout_at = (datetime.utcnow() + timedelta(seconds=timeout_s)).strftime("%Y-%m-%dT%H:%M:%S")
            _set_task_stage(task_id, "scan_requested", scan_deadline_at=timeout_at)

    tasks = _list_tasks(stages=("scan_requested",), limit=limit)
    if not tasks:
        return {"in_library": 0, "timed_out": 0, "waiting": 0}

    _run_stage1_sync_once()
    in_library = 0
    timed_out = 0
    waiting = 0

    now_dt = datetime.utcnow()
    for task in tasks:
        if _confirm_task_in_library(task):
            in_library += 1
            continue

        deadline_raw = str(task.get("scan_deadline_at") or "").strip()
        if deadline_raw:
            try:
                if now_dt >= datetime.fromisoformat(deadline_raw):
                    _set_task_stage(
                        int(task["id"]),
                        "failed",
                        error_type="recoverable",
                        error_code="scan_timeout",
                        error_message="Timed out waiting for library registration after move.",
                    )
                    timed_out += 1
                    continue
            except ValueError:
                pass
        waiting += 1

    return {"in_library": in_library, "timed_out": timed_out, "waiting": waiting}


def start_fetch_run(build_id: str, *, triggered_by: str = "manual") -> dict[str, Any]:
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        raise ValueError("Build not found")

    # Idempotency guard: do not create overlapping runs for the same build.
    with rythmx_store._connect() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM fetch_runs
            WHERE build_id = ? AND status = 'running'
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (build_id,),
        ).fetchone()
    if existing:
        summary = _reconcile_run(str(existing["id"]))
        summary["existing_run"] = True
        return summary

    downloader = _plugins.get_downloader()
    provider = str(getattr(downloader, "name", "stub"))
    run_id = str(uuid.uuid4())
    now = _utcnow()
    candidates = _build_fetch_candidates(build)

    with rythmx_store._connect() as conn:
        conn.execute(
            """
            INSERT INTO fetch_runs
                (id, build_id, provider, status, triggered_by, total_tasks, config_json, started_at, created_at, updated_at)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                build_id,
                provider,
                triggered_by,
                len(candidates),
                json.dumps({"fetch_wait_timeout_s": _fetch_wait_timeout_s()}),
                now,
                now,
                now,
            ),
        )

        for item in candidates:
            conn.execute(
                """
                INSERT INTO fetch_tasks
                    (run_id, build_id, provider, artist_name, album_name, artist_key, album_key,
                     stage, metadata_json, created_at, updated_at, last_transition_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    build_id,
                    provider,
                    item["artist_name"],
                    item["album_name"],
                    item["artist_key"],
                    item["album_key"],
                    json.dumps(item["metadata"]),
                    now,
                    now,
                    now,
                ),
            )

    submit_result = _submit_queued_tasks(run_id=run_id, limit=200)
    summary = _reconcile_run(run_id)
    summary["submission"] = submit_result
    return summary


def get_build_fetch_status(build_id: str) -> dict[str, Any]:
    with rythmx_store._connect() as conn:
        run = conn.execute(
            """
            SELECT id
            FROM fetch_runs
            WHERE build_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (build_id,),
        ).fetchone()
    if not run:
        return {
            "build_id": build_id,
            "run": None,
            "stage_counts": {},
            "jobs": rythmx_store.get_download_jobs_for_build(build_id),
        }

    run_id = str(run["id"])
    summary = _run_summary(run_id)
    tasks = _list_tasks(run_id=run_id, limit=1000)
    with rythmx_store._connect() as conn:
        scan_timeout = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM fetch_tasks
            WHERE run_id = ? AND error_code = 'scan_timeout'
            """,
            (run_id,),
        ).fetchone()
    stage_counts = summary.get("stage_counts", {})
    jobs = rythmx_store.get_download_jobs_for_build(build_id)
    return {
        "build_id": build_id,
        "run": summary,
        "stage_counts": stage_counts,
        "tasks": tasks,
        "confirmation": {
            "timeout_s": _fetch_wait_timeout_s(),
            "waiting": int(stage_counts.get("scan_requested", 0)),
            "confirmed": int(stage_counts.get("in_library", 0)),
            "timed_out": int((scan_timeout["cnt"] if scan_timeout else 0) or 0),
        },
        # Compatibility fields for clients that still read download_jobs aggregates.
        "total": len(jobs),
        "pending": sum(1 for j in jobs if str(j.get("status") or "") == "pending"),
        "completed": sum(1 for j in jobs if str(j.get("status") or "") == "completed"),
        "failed": sum(1 for j in jobs if str(j.get("status") or "") == "failed"),
        "jobs": jobs,
    }


def list_fetch_runs(
    *,
    status: str | None = None,
    provider: str | None = None,
    build_source: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("fr.status = ?")
        params.append(status)
    if provider:
        where.append("fr.provider = ?")
        params.append(provider)
    if build_source:
        where.append("fb.source = ?")
        params.append(build_source)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT fr.id
            FROM fetch_runs fr
            LEFT JOIN forge_builds fb ON fb.id = fr.build_id
            {where_sql}
            ORDER BY datetime(fr.created_at) DESC
            LIMIT ?
            """,
            tuple(params + [max(1, min(limit, 500))]),
        ).fetchall()

    return [_run_summary(str(r["id"])) for r in rows]


def get_fetch_run(run_id: str) -> dict[str, Any] | None:
    summary = _run_summary(run_id)
    return summary or None


def list_fetch_tasks_for_run(
    run_id: str,
    *,
    stage: str | None = None,
    provider: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    where = ["run_id = ?"]
    params: list[Any] = [run_id]
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if provider:
        where.append("provider = ?")
        params.append(provider)

    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM fetch_tasks
            WHERE {' AND '.join(where)}
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            """,
            tuple(params + [max(1, min(limit, 10000))]),
        ).fetchall()
    return [_task_from_row(dict(r)) for r in rows]


def retry_fetch_run(run_id: str, *, task_ids: list[int] | None = None) -> dict[str, Any]:
    summary = _run_summary(run_id)
    if not summary:
        raise ValueError("Fetch run not found")

    with rythmx_store._connect() as conn:
        args: list[Any] = [run_id]
        task_filter_sql = "run_id = ? AND stage IN ('failed', 'unresolved')"
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            task_filter_sql += f" AND id IN ({placeholders})"
            args.extend(task_ids)

        candidates = conn.execute(
            f"""
            SELECT id, retry_count
            FROM fetch_tasks
            WHERE {task_filter_sql}
            """,
            tuple(args),
        ).fetchall()

        now = _utcnow()
        retried = 0
        for row in candidates:
            conn.execute(
                """
                UPDATE fetch_tasks
                SET stage = 'queued',
                    retry_count = ?,
                    provider_job_id = NULL,
                    storage_path = NULL,
                    source_dir = NULL,
                    dest_dir = NULL,
                    error_type = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    scan_deadline_at = NULL,
                    completed_at = NULL,
                    updated_at = ?,
                    last_transition_at = ?
                WHERE id = ?
                """,
                (int(row["retry_count"] or 0) + 1, now, now, int(row["id"])),
            )
            retried += 1

        if retried > 0:
            conn.execute(
                """
                UPDATE fetch_runs
                SET status = 'running',
                    finished_at = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, run_id),
            )

    submission = _submit_queued_tasks(run_id=run_id, limit=500)
    refreshed = _reconcile_run(run_id)
    return {
        "run": refreshed,
        "retried": retried,
        "submission": submission,
    }


def poll_once() -> dict[str, Any]:
    """
    Generic fetch worker tick.
    Replaces provider-specific poll ownership loops.
    """
    submitted = _submit_queued_tasks(limit=500)
    provider = _poll_provider_updates(limit=1000)
    downloaded = _process_downloaded_tasks(limit=500)
    scan = _process_scan_requests(limit=500)

    with rythmx_store._connect() as conn:
        run_rows = conn.execute(
            """
            SELECT id
            FROM fetch_runs
            WHERE status = 'running'
            ORDER BY datetime(created_at) ASC
            LIMIT 500
            """
        ).fetchall()

    reconciled: list[dict[str, Any]] = []
    for row in run_rows:
        reconciled.append(_reconcile_run(str(row["id"])))

    return {
        "checked": len(run_rows),
        "submitted": submitted,
        "provider": provider,
        "downloaded": downloaded,
        "scan": scan,
        "runs": reconciled,
    }
