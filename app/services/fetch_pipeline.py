"""
fetch_pipeline.py - Provider-agnostic fetch control plane (Tidarr first).
"""
from __future__ import annotations

import glob as _glob
import json
import logging
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Any

from app import plugins as _plugins
from app.db import get_library_reader
from app.db import rythmx_store
from app.plugins import DownloadArtifact
from app.routes.ws import broadcast
from app.services.api_orchestrator import EnrichmentOrchestrator

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

FETCH_RUN_STATUSES = ("running", "completed", "failed")
FETCH_QUEUE_STATUSES = ("pending", "running", "completed", "failed", "canceled")
FETCH_HANDOFF_STATUSES = ("idle", "enriching", "confirming", "done", "failed")

_TERMINAL_STAGES = frozenset({"in_library", "failed", "unresolved"})
_ACTIVE_STAGES = tuple(stage for stage in FETCH_STAGES if stage not in _TERMINAL_STAGES)
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed"})
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


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"\((?:feat|ft|featuring)[^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:feat|ft|featuring)\.?\s+.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    def _maybe_track_title(item: dict[str, Any], album_name: str) -> str:
        for key in ("track_name", "track_title", "name", "title"):
            value = str(item.get(key) or "").strip()
            if value:
                break
        else:
            value = ""
        if not value:
            return ""
        # In some Forge payloads `title` is the album title; avoid polluting track hints.
        if _normalize_text(value) == _normalize_text(album_name):
            return ""
        return value

    def _coerce_tidal_id(item: dict[str, Any]) -> str | None:
        for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
            raw = str(item.get(key) or "").strip()
            if raw.isdigit():
                return raw
        return None

    def _coerce_track_count(item: dict[str, Any]) -> int | None:
        for key in ("track_count", "total_tracks"):
            raw = str(item.get(key) or "").strip()
            if not raw:
                continue
            try:
                return int(float(raw))
            except Exception:
                continue
        return None

    for idx, item in enumerate(track_list):
        if not isinstance(item, dict):
            continue

        artist = str(item.get("artist_name") or "").strip()
        album = str(item.get("album_name") or item.get("title") or "").strip()
        if not artist or not album:
            continue

        key = (artist.lower(), album.lower())
        entry = grouped.get(key)
        if not entry:
            entry = {
                "artist_name": artist,
                "album_name": album,
                "artist_key": key[0],
                "album_key": key[1],
                "track_titles_set": set(),
                "metadata": {
                    "deezer_album_id": item.get("deezer_album_id") or item.get("deezer_id"),
                    "itunes_album_id": item.get("itunes_album_id"),
                    "spotify_album_id": item.get("spotify_album_id"),
                    "musicbrainz_release_id": item.get("musicbrainz_release_id"),
                    "release_date": item.get("release_date"),
                    "thumb_url": item.get("thumb_url") or item.get("cover_url"),
                    "item_index": idx,
                    "tidal_album_id": _coerce_tidal_id(item),
                    "track_count": _coerce_track_count(item),
                },
            }
            grouped[key] = entry

        metadata = entry["metadata"]
        if not metadata.get("deezer_album_id"):
            metadata["deezer_album_id"] = item.get("deezer_album_id") or item.get("deezer_id")
        if not metadata.get("itunes_album_id"):
            metadata["itunes_album_id"] = item.get("itunes_album_id")
        if not metadata.get("spotify_album_id"):
            metadata["spotify_album_id"] = item.get("spotify_album_id")
        if not metadata.get("musicbrainz_release_id"):
            metadata["musicbrainz_release_id"] = item.get("musicbrainz_release_id")
        if not metadata.get("release_date"):
            metadata["release_date"] = item.get("release_date")
        if not metadata.get("thumb_url"):
            metadata["thumb_url"] = item.get("thumb_url") or item.get("cover_url")
        if not metadata.get("tidal_album_id"):
            metadata["tidal_album_id"] = _coerce_tidal_id(item)

        tc = _coerce_track_count(item)
        if tc is not None:
            existing_tc = metadata.get("track_count")
            try:
                existing_tc_i = int(existing_tc) if existing_tc is not None else 0
            except Exception:
                existing_tc_i = 0
            metadata["track_count"] = max(existing_tc_i, tc)

        maybe_title = _maybe_track_title(item, album)
        if maybe_title:
            entry["track_titles_set"].add(maybe_title)

    out: list[dict[str, Any]] = []
    for entry in grouped.values():
        metadata = dict(entry.get("metadata") or {})
        if _is_library_owned_for_fetch_candidate(
            str(entry.get("artist_name") or ""),
            str(entry.get("album_name") or ""),
            metadata,
        ):
            continue
        track_titles = sorted(str(v) for v in entry.pop("track_titles_set", set()) if str(v).strip())
        if track_titles:
            entry["metadata"]["track_titles"] = track_titles[:100]
            if not entry["metadata"].get("track_count"):
                entry["metadata"]["track_count"] = len(track_titles)
        out.append(entry)
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


def _split_path_parts(path: str) -> list[str]:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return []
    return [part for part in raw.split("/") if part]


def _build_source_candidates(storage_path: str, downloader: Any) -> list[str]:
    candidates: list[str] = []

    def _add(path: str | None) -> None:
        if path is None:
            return
        raw = str(path).strip()
        if not raw:
            return
        normalized = os.path.normpath(raw)
        if normalized not in candidates:
            candidates.append(normalized)

    _add(storage_path)

    translate = getattr(downloader, "translate_path", None)
    if callable(translate):
        try:
            _add(translate(storage_path))
        except Exception as exc:
            logger.warning("fetch_pipeline: downloader.translate_path failed for '%s': %s", storage_path, exc)

    local_prefix = str(
        getattr(downloader, "_local_prefix", "") or os.environ.get("FILE_MOVER_LOCAL_PREFIX") or ""
    ).strip().rstrip("/\\")
    remote_prefix = str(
        getattr(downloader, "_tidarr_prefix", "") or os.environ.get("FILE_MOVER_TIDARR_PREFIX") or ""
    ).strip().rstrip("/\\")

    if local_prefix and remote_prefix and storage_path.startswith(remote_prefix):
        _add(local_prefix + storage_path[len(remote_prefix):])

    if local_prefix:
        for source in tuple(candidates):
            parts = _split_path_parts(source)
            if len(parts) > 1 and not parts[0].endswith(":"):
                _add(os.path.join(local_prefix, *parts[1:]))

    if str(storage_path or "").startswith("/downloads/"):
        _add(f"/app{storage_path}")

    return candidates


def _resolve_source_dir(storage_path: str, downloader: Any) -> tuple[str | None, list[str]]:
    candidates = _build_source_candidates(storage_path, downloader)
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate, candidates
    return None, candidates


def _task_from_row(row: dict[str, Any]) -> dict[str, Any]:
    task = dict(row)
    task["metadata"] = _safe_json(task.get("metadata_json"), {})
    task["match_reasons"] = _safe_json(task.get("match_reasons_json"), [])
    task["match_candidates"] = _safe_json(task.get("match_candidates_json"), [])
    return task


def _queue_from_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _safe_json(item.get("payload_json"), {})
    return item


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


def _set_task_match(
    task_id: int,
    *,
    status: str | None = None,
    strategy: str | None = None,
    confidence: float | None = None,
    reasons: list[str] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if status is not None:
        payload["match_status"] = str(status or "").strip() or None
    if strategy is not None:
        payload["match_strategy"] = str(strategy or "").strip() or None
    if confidence is not None:
        try:
            payload["match_confidence"] = float(confidence)
        except Exception:
            payload["match_confidence"] = None
    if reasons is not None:
        payload["match_reasons_json"] = json.dumps(list(reasons), ensure_ascii=True)
    if candidates is not None:
        payload["match_candidates_json"] = json.dumps(list(candidates), ensure_ascii=True)
    if payload:
        _set_task_fields(task_id, **payload)


def _set_run_fields(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    now = _utcnow()
    updates = []
    params: list[Any] = []
    for key, value in fields.items():
        updates.append(f"{key} = ?")
        params.append(value)
    updates.append("updated_at = ?")
    params.extend([now, run_id])

    with rythmx_store._connect() as conn:
        conn.execute(
            f"UPDATE fetch_runs SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )


def _set_queue_fields(queue_id: str, **fields: Any) -> None:
    if not fields:
        return
    now = _utcnow()
    updates = []
    params: list[Any] = []
    for key, value in fields.items():
        updates.append(f"{key} = ?")
        params.append(value)
    updates.append("updated_at = ?")
    params.extend([now, queue_id])
    with rythmx_store._connect() as conn:
        conn.execute(
            f"UPDATE fetch_queue SET {', '.join(updates)} WHERE id = ?",
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
    run["terminal_emitted"] = bool(int(run.get("terminal_emitted") or 0))
    run["cancel_requested"] = bool(int(run.get("cancel_requested") or 0))
    return run


def _queue_summary(queue_id: str) -> dict[str, Any] | None:
    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT fq.*,
                   fb.source AS build_source,
                   fb.name AS build_name,
                   fb.status AS build_status,
                   fr.status AS run_status,
                   fr.handoff_status AS run_handoff_status
            FROM fetch_queue fq
            LEFT JOIN forge_builds fb ON fb.id = fq.build_id
            LEFT JOIN fetch_runs fr ON fr.id = fq.run_id
            WHERE fq.id = ?
            """,
            (queue_id,),
        ).fetchone()
    return _queue_from_row(dict(row)) if row else None


def _mark_build_item_in_library(
    build_id: str,
    artist_name: str,
    album_name: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        return

    metadata = dict(metadata or {})
    expected_ids: set[str] = set()
    for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
        raw = str(metadata.get(key) or "").strip()
        if raw.isdigit():
            expected_ids.add(raw)

    def _item_tidal_ids(item: dict[str, Any]) -> set[str]:
        out: set[str] = set()
        for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
            raw = str(item.get(key) or "").strip()
            if raw.isdigit():
                out.add(raw)
        return out

    track_list = list(build.get("track_list") or [])
    changed = False
    for item in track_list:
        if not isinstance(item, dict):
            continue
        artist = str(item.get("artist_name") or "").strip().lower()
        album = str(item.get("album_name") or item.get("title") or "").strip().lower()
        matches = False
        if expected_ids:
            matches = bool(_item_tidal_ids(item) & expected_ids)
        if not expected_ids:
            matches = artist == artist_name.lower() and album == album_name.lower()
        if matches:
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


def _has_library_ids(metadata: dict[str, Any]) -> bool:
    return any(
        str(metadata.get(key) or "").strip()
        for key in ("deezer_album_id", "itunes_album_id", "spotify_album_id", "musicbrainz_release_id")
    )


def _check_library_ids(metadata: dict[str, Any]) -> bool:
    deezer_album_id = str(metadata.get("deezer_album_id") or "").strip()
    itunes_album_id = str(metadata.get("itunes_album_id") or "").strip()
    spotify_album_id = str(metadata.get("spotify_album_id") or "").strip()
    musicbrainz_release_id = str(metadata.get("musicbrainz_release_id") or "").strip()

    clauses: list[str] = []
    params: list[Any] = []
    if deezer_album_id:
        clauses.append("deezer_album_id = ?")
        params.append(deezer_album_id)
    if itunes_album_id:
        clauses.append("itunes_album_id = ?")
        params.append(itunes_album_id)
    if spotify_album_id:
        clauses.append("spotify_album_id = ?")
        params.append(spotify_album_id)
    if musicbrainz_release_id:
        clauses.append("musicbrainz_release_group_id = ?")
        params.append(musicbrainz_release_id)

    if not clauses:
        return False

    with rythmx_store._connect() as conn:
        rel_row = conn.execute(
            f"""
            SELECT id
            FROM lib_releases
            WHERE is_owned = 1
              AND ({' OR '.join(clauses)})
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return bool(rel_row)


def _is_library_owned_for_fetch_candidate(artist_name: str, album_name: str, metadata: dict[str, Any]) -> bool:
    metadata = dict(metadata or {})
    if _has_library_ids(metadata):
        return _check_library_ids(metadata)
    if _check_library_exact(artist_name, album_name):
        return True
    if _check_library_normalized(artist_name, album_name):
        return True
    return False


def _check_library_exact(artist_name: str, album_name: str) -> bool:
    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT la.id
            FROM lib_albums la
            JOIN lib_artists ar ON ar.id = la.artist_id
            WHERE ar.removed_at IS NULL
              AND la.removed_at IS NULL
              AND lower(ar.name) = lower(?)
              AND lower(la.title) = lower(?)
            LIMIT 1
            """,
            (artist_name, album_name),
        ).fetchone()
        return bool(row)


def _check_library_normalized(artist_name: str, album_name: str) -> bool:
    normalized_artist = _normalize_text(artist_name)
    normalized_album = _normalize_text(album_name)
    if not normalized_artist or not normalized_album:
        return False
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT ar.name AS artist_name, la.title AS album_title
            FROM lib_albums la
            JOIN lib_artists ar ON ar.id = la.artist_id
            WHERE ar.removed_at IS NULL
              AND la.removed_at IS NULL
              AND lower(ar.name) = lower(?)
            """,
            (artist_name,),
        ).fetchall()
    for row in rows:
        if _normalize_text(str(row["artist_name"] or "")) != normalized_artist:
            continue
        if _normalize_text(str(row["album_title"] or "")) == normalized_album:
            return True
    return False


def _confirm_task_in_library(task: dict[str, Any]) -> bool:
    metadata = dict(task.get("metadata") or {})
    artist = str(task.get("artist_name") or "")
    album = str(task.get("album_name") or "")
    matched = False

    if _has_library_ids(metadata):
        matched = _check_library_ids(metadata)
    else:
        try:
            reader = get_library_reader()
            matched = bool(reader.check_album_owned(artist, album))
        except Exception:
            matched = False
        if not matched:
            matched = _check_library_exact(artist, album)
        if not matched:
            matched = _check_library_normalized(artist, album)
    if not matched:
        return False

    _set_task_stage(int(task["id"]), "in_library")
    _mark_build_item_in_library(
        str(task.get("build_id") or ""),
        artist,
        album,
        metadata,
    )
    return True


def _validate_enrichment_result(result: dict[str, Any]) -> dict[str, Any]:
    """
    Validate pre-fetch enrichment result to ensure only safe primitives are returned.

    Rejects:
    - Unknown keys (not in SAFE_ENRICHMENT_KEYS allowlist)
    - Non-primitive values (lists, dicts, objects)
    - Suspicious values that look like tokens/URLs

    Returns sanitized dict ready to merge into task metadata.
    """
    SAFE_ENRICHMENT_KEYS = {
        "tidal_album_id",
        "deezer_album_id",
        "itunes_album_id",
        "spotify_album_id",
        "musicbrainz_release_id",
        "release_date",
        "thumb_url",
        "track_count",
    }
    TOKEN_PATTERNS = [
        r"^eyJ",  # JWT (starts with eyJ...)
        r"^[a-f0-9]{64,}",  # API key (64+ hex chars)
        r"https?://",  # URL
    ]

    if not isinstance(result, dict):
        return {}

    safe: dict[str, Any] = {}
    for key, value in result.items():
        # Only allow whitelisted keys
        if key not in SAFE_ENRICHMENT_KEYS:
            logger.debug("enrichment: rejecting unknown key '%s'", key)
            continue

        # Only allow safe primitives
        if not isinstance(value, (str, int, float, bool, type(None))):
            logger.debug("enrichment: rejecting non-primitive value for '%s' (type %s)", key, type(value).__name__)
            continue

        # Reject suspicious string values that look like tokens or URLs
        if isinstance(value, str) and any(re.match(p, value) for p in TOKEN_PATTERNS):
            logger.warning("enrichment: rejecting suspicious value for '%s' (looks like token/URL)", key)
            continue

        safe[key] = value

    return safe


def _submit_queued_tasks(*, run_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    params: list[Any] = []
    where = [
        "ft.stage = 'queued'",
        "fr.status = 'running'",
        "fr.cancel_requested = 0",
    ]
    if run_id:
        where.append("ft.run_id = ?")
        params.append(run_id)

    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ft.*
            FROM fetch_tasks ft
            JOIN fetch_runs fr ON fr.id = ft.run_id
            WHERE {' AND '.join(where)}
            ORDER BY ft.created_at ASC
            LIMIT ?
            """,
            tuple(params + [limit]),
        ).fetchall()
    tasks = [_task_from_row(dict(r)) for r in rows]
    if not tasks:
        return {"submitted": 0, "unresolved": 0, "failed": 0, "jobs": []}

    downloader = _plugins.get_downloader()

    # ========================================================================
    # PHASE 1: RESOLVE ALL (Tidal API only - no Tidarr calls)
    # ========================================================================
    logger.info("fetch_pipeline: phase 1 starting - resolve %d tasks via pre-fetch enrichment", len(tasks))

    for task in tasks:
        task_id = int(task["id"])
        artist = str(task.get("artist_name") or "")
        album = str(task.get("album_name") or "")
        metadata = task.get("metadata") or {}

        # Pre-fetch enrichment (if plugin provides it)
        if hasattr(downloader, "pre_fetch_enrich") and callable(downloader.pre_fetch_enrich):
            try:
                enrichment = downloader.pre_fetch_enrich(artist, album, metadata)
                if isinstance(enrichment, dict):
                    # Validate: only safe primitives
                    validated_enrichment = _validate_enrichment_result(enrichment)
                    if validated_enrichment:
                        metadata = dict(metadata)  # Ensure dict, not read-only
                        metadata.update(validated_enrichment)
                        # Persist enriched metadata to DB
                        metadata_json = json.dumps(metadata, ensure_ascii=True)
                        _set_task_fields(task_id, metadata_json=metadata_json)
                        logger.info(
                            "fetch_pipeline: phase 1 - enriched %s - %s: added keys %s",
                            artist,
                            album,
                            list(validated_enrichment.keys()),
                        )
            except Exception as e:
                # Non-fatal: log and continue
                logger.warning("fetch_pipeline: phase 1 - pre_fetch_enrich failed for %s - %s: %s", artist, album, e)

    logger.info("fetch_pipeline: phase 1 complete - all tasks enriched, proceeding to phase 2")

    # ========================================================================
    # PHASE 2: SUBMIT ALL (Tidarr only - with resolved IDs)
    # ========================================================================
    logger.info("fetch_pipeline: phase 2 starting - submit %d tasks to Tidarr", len(tasks))

    submitted = 0
    unresolved = 0
    failed = 0
    jobs: list[dict[str, str]] = []

    for task in tasks:
        task_id = int(task["id"])
        artist = str(task.get("artist_name") or "")
        album = str(task.get("album_name") or "")
        # Re-fetch metadata from DB to get enriched version
        with rythmx_store._connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM fetch_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        metadata = {}
        if row and row[0]:
            try:
                metadata = json.loads(row[0])
            except Exception:
                metadata = {}

        job_id = ""
        match_status = ""
        match_strategy = ""
        match_confidence: float | None = None
        match_reasons: list[str] = []
        match_candidates: list[dict[str, Any]] = []
        outcome_error = ""

        try:
            submit_with_match = getattr(downloader, "submit_with_match", None)
            if callable(submit_with_match):
                outcome = submit_with_match(artist, album, metadata)
                if isinstance(outcome, dict):
                    job_id = str(outcome.get("job_id") or "")
                    match_status = str(outcome.get("match_status") or outcome.get("status") or "").strip()
                    match_strategy = str(outcome.get("match_strategy") or "").strip()
                    try:
                        match_confidence = (
                            float(outcome["match_confidence"])
                            if outcome.get("match_confidence") is not None
                            else None
                        )
                    except Exception:
                        match_confidence = None
                    raw_reasons = outcome.get("match_reasons")
                    if isinstance(raw_reasons, list):
                        match_reasons = [str(v) for v in raw_reasons][:20]
                    raw_candidates = outcome.get("candidates")
                    if isinstance(raw_candidates, list):
                        match_candidates = [
                            dict(v) for v in raw_candidates[:20] if isinstance(v, dict)
                        ]
                    outcome_error = str(outcome.get("error_message") or "").strip()
                else:
                    job_id = str(outcome or "")
            else:
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

        if match_status or match_strategy or match_confidence is not None or match_reasons or match_candidates:
            _set_task_match(
                task_id,
                status=match_status or None,
                strategy=match_strategy or None,
                confidence=match_confidence,
                reasons=match_reasons,
                candidates=match_candidates,
            )

        if not job_id or job_id.startswith("unresolved:"):
            unresolved_code = "unresolved"
            if match_status == "ambiguous":
                unresolved_code = "ambiguous_match"
            elif match_status == "search_inconsistent":
                unresolved_code = "search_inconsistent"
            _set_task_stage(
                task_id,
                "unresolved",
                error_type="permanent",
                error_code=unresolved_code,
                error_message=outcome_error or job_id or f"unresolved:{artist}:{album}",
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

    logger.info("fetch_pipeline: phase 2 complete - submitted=%d unresolved=%d failed=%d", submitted, unresolved, failed)
    return {"submitted": submitted, "unresolved": unresolved, "failed": failed, "jobs": jobs}


def _poll_provider_updates(limit: int = 500) -> dict[str, int]:
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT ft.*
            FROM fetch_tasks ft
            JOIN fetch_runs fr ON fr.id = ft.run_id
            WHERE ft.stage IN ('submitted', 'downloading')
              AND fr.status = 'running'
              AND fr.cancel_requested = 0
            ORDER BY ft.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    tasks = [_task_from_row(dict(r)) for r in rows]
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
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT ft.*
            FROM fetch_tasks ft
            JOIN fetch_runs fr ON fr.id = ft.run_id
            WHERE ft.stage IN ('downloaded', 'tagged')
              AND fr.status = 'running'
              AND fr.cancel_requested = 0
            ORDER BY ft.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    tasks = [_task_from_row(dict(r)) for r in rows]
    if not tasks:
        return {"tagged": 0, "moved": 0, "failed": 0}

    downloader = _plugins.get_downloader()
    tagger = _plugins.get_tagger()
    file_handler = _plugins.get_file_handler()
    result = {"tagged": 0, "moved": 0, "failed": 0}

    for task in tasks:
        task_id = int(task["id"])
        storage_path = str(task.get("storage_path") or "")
        local_path, attempted = _resolve_source_dir(storage_path, downloader)

        if not local_path:
            attempted_text = ", ".join(attempted[:4]) if attempted else storage_path
            message = (
                f"Source path not accessible: {storage_path} (tried: {attempted_text}). "
                "Set FILE_MOVER_TIDARR_PREFIX and FILE_MOVER_LOCAL_PREFIX if provider/container paths differ."
            )
            _set_task_stage(
                task_id,
                "failed",
                error_type="recoverable",
                error_code="source_not_accessible",
                error_message=message,
                source_dir=attempted[0] if attempted else storage_path,
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


def _advance_moved_to_scan_requested(limit: int = 500) -> dict[str, int]:
    moved = _list_tasks(stages=("moved",), limit=limit)
    if not moved:
        return {"scan_requested": 0}
    timeout_s = _fetch_wait_timeout_s()
    moved_count = 0
    for task in moved:
        task_id = int(task["id"])
        timeout_at = (datetime.utcnow() + timedelta(seconds=timeout_s)).strftime("%Y-%m-%dT%H:%M:%S")
        _set_task_stage(task_id, "scan_requested", scan_deadline_at=timeout_at)
        moved_count += 1
    return {"scan_requested": moved_count}


def _process_run_cancel_requests(run_id: str) -> dict[str, int]:
    summary = _run_summary(run_id)
    if not summary or not bool(summary.get("cancel_requested")):
        return {"canceled_tasks": 0}

    tasks = _list_tasks(run_id=run_id, limit=10000)
    canceled = 0
    for task in tasks:
        stage = str(task.get("stage") or "")
        if stage in _TERMINAL_STAGES:
            continue
        _set_task_stage(
            int(task["id"]),
            "failed",
            error_type="recoverable",
            error_code="run_canceled",
            error_message="Canceled by user",
        )
        provider_job_id = str(task.get("provider_job_id") or "").strip()
        if provider_job_id:
            rythmx_store.update_download_job_status(provider_job_id, "failed")
        canceled += 1

    if canceled > 0:
        _set_run_fields(
            run_id,
            handoff_status="failed",
            handoff_error="Canceled by user",
            handoff_finished_at=_utcnow(),
        )
    return {"canceled_tasks": canceled}


def _process_run_handoff(run_id: str) -> dict[str, int]:
    summary = _run_summary(run_id)
    if not summary or str(summary.get("status") or "") != "running":
        return {"in_library": 0, "timed_out": 0, "waiting": 0}
    if bool(summary.get("cancel_requested")):
        return {"in_library": 0, "timed_out": 0, "waiting": 0}

    handoff_status = str(summary.get("handoff_status") or "idle")
    scan_tasks = _list_tasks(run_id=run_id, stages=("scan_requested",), limit=5000)
    if not scan_tasks:
        if handoff_status in {"enriching", "confirming"}:
            _set_run_fields(
                run_id,
                handoff_status="done",
                handoff_finished_at=_utcnow(),
                handoff_error=None,
            )
        return {"in_library": 0, "timed_out": 0, "waiting": 0}

    try:
        orchestrator = EnrichmentOrchestrator.get()
        if handoff_status == "idle":
            with _SCAN_LOCK:
                orchestrator.run_full(batch_size=10_000)
            _set_run_fields(
                run_id,
                handoff_status="enriching",
                handoff_started_at=summary.get("handoff_started_at") or _utcnow(),
                handoff_error=None,
            )
            return {"in_library": 0, "timed_out": 0, "waiting": len(scan_tasks)}

        if handoff_status == "enriching":
            if orchestrator.is_running():
                return {"in_library": 0, "timed_out": 0, "waiting": len(scan_tasks)}
            _set_run_fields(run_id, handoff_status="confirming")

        in_library = 0
        timed_out = 0
        waiting = 0
        now_dt = datetime.utcnow()
        for task in scan_tasks:
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

        if waiting == 0:
            _set_run_fields(
                run_id,
                handoff_status="done",
                handoff_finished_at=_utcnow(),
                handoff_error=None,
            )
        else:
            _set_run_fields(run_id, handoff_status="confirming")

        return {"in_library": in_library, "timed_out": timed_out, "waiting": waiting}
    except Exception as exc:
        _set_run_fields(
            run_id,
            handoff_status="failed",
            handoff_error=str(exc),
            handoff_finished_at=_utcnow(),
        )
        logger.warning("fetch_pipeline: handoff processing failed for run %s: %s", run_id, exc)
        return {"in_library": 0, "timed_out": 0, "waiting": len(scan_tasks)}


def _reconcile_run(run_id: str) -> dict[str, Any]:
    summary = _run_summary(run_id)
    if not summary:
        return {}

    total = int(summary.get("total_tasks", 0))
    failed = int(summary.get("failed", 0))
    unresolved = int(summary.get("unresolved", 0))
    active = int(summary.get("active_tasks", 0))
    in_library = int(summary.get("in_library", 0))
    cancel_requested = bool(summary.get("cancel_requested"))
    terminal_emitted = bool(summary.get("terminal_emitted"))

    if total == 0:
        next_status = "completed"
    elif active > 0:
        next_status = "running"
    elif failed + unresolved > 0:
        next_status = "failed"
    else:
        next_status = "completed"

    now = _utcnow()
    next_last_error = None
    if next_status == "failed":
        if cancel_requested:
            next_last_error = "Fetch run canceled by user"
        else:
            next_last_error = str(summary.get("last_error") or "").strip() or "Fetch run ended with unresolved failures"

    _set_run_fields(
        run_id,
        status=next_status,
        total_tasks=total,
        finished_at=(now if next_status in _TERMINAL_RUN_STATUSES else None),
        last_error=next_last_error,
        handoff_status=(
            "failed"
            if next_status == "failed" and str(summary.get("handoff_status") or "") != "done"
            else summary.get("handoff_status")
        ),
    )

    if next_status in _TERMINAL_RUN_STATUSES and not terminal_emitted:
        if next_status == "completed":
            _emit_complete(
                run_id=run_id,
                summary={
                    "status": next_status,
                    "in_library": in_library,
                    "failed": failed,
                    "unresolved": unresolved,
                    "total": total,
                },
            )
        else:
            _emit_error(
                run_id=run_id,
                message=f"Fetch run failed ({failed} failed, {unresolved} unresolved).",
            )
        _set_run_fields(run_id, terminal_emitted=1)
    elif next_status == "running":
        _emit_progress(
            run_id=run_id,
            stage="running",
            processed=summary.get("processed_tasks", 0),
            total=total,
            message=f"{in_library} in library, {failed} failed, {unresolved} unresolved",
        )

    return _run_summary(run_id)


def _sync_queue_for_terminal_run(run: dict[str, Any]) -> None:
    queue_id = str(run.get("queue_id") or "").strip()
    if not queue_id:
        return
    queue = _queue_summary(queue_id)
    if not queue:
        return
    if str(queue.get("status") or "") in {"completed", "failed", "canceled"}:
        return

    run_status = str(run.get("status") or "")
    if run_status == "completed":
        queue_status = "completed"
        queue_error = None
    elif bool(run.get("cancel_requested")):
        queue_status = "canceled"
        queue_error = str(run.get("last_error") or "Canceled by user")
    else:
        queue_status = "failed"
        queue_error = str(run.get("last_error") or "Fetch run failed")
    _set_queue_fields(
        queue_id,
        status=queue_status,
        finished_at=_utcnow(),
        last_error=queue_error,
    )


def _has_running_fetch_run() -> bool:
    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM fetch_runs
            WHERE status = 'running'
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """
        ).fetchone()
    return bool(row)


def _start_next_pending_queue_item() -> dict[str, Any] | None:
    if _has_running_fetch_run():
        return None

    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM fetch_queue
            WHERE status = 'pending'
            ORDER BY queue_position ASC, datetime(created_at) ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        item = _queue_from_row(dict(row))
        now = _utcnow()
        conn.execute(
            """
            UPDATE fetch_queue
            SET status = 'running',
                started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, item["id"]),
        )

    try:
        run = start_fetch_run(
            str(item["build_id"]),
            triggered_by=str(item.get("requested_by") or "manual"),
            queue_id=str(item["id"]),
        )
    except Exception as exc:
        logger.warning("fetch_pipeline: queue item %s failed to start: %s", item["id"], exc)
        _set_queue_fields(
            str(item["id"]),
            status="failed",
            finished_at=_utcnow(),
            last_error=str(exc),
        )
        return None

    if run.get("status") in _TERMINAL_RUN_STATUSES:
        _sync_queue_for_terminal_run(run)
    return run


def start_fetch_run(
    build_id: str,
    *,
    triggered_by: str = "manual",
    queue_id: str | None = None,
) -> dict[str, Any]:
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        raise ValueError("Build not found")

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
        if queue_id:
            _set_run_fields(str(existing["id"]), queue_id=queue_id)
            _set_queue_fields(
                queue_id,
                status="running",
                run_id=str(existing["id"]),
                started_at=_utcnow(),
                last_error=None,
            )
            summary = _run_summary(str(existing["id"]))
        summary["existing_run"] = True
        return summary

    downloader = _plugins.get_downloader()
    provider = str(getattr(downloader, "name", "stub"))
    run_id = str(uuid.uuid4())
    now = _utcnow()
    candidates = _build_fetch_candidates(build)
    timeout_cfg = {"fetch_wait_timeout_s": _fetch_wait_timeout_s()}

    with rythmx_store._connect() as conn:
        conn.execute(
            """
            INSERT INTO fetch_runs
                (id, build_id, queue_id, provider, status, triggered_by, total_tasks, config_json,
                 started_at, created_at, updated_at, handoff_status, terminal_emitted, cancel_requested)
            VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, 'idle', 0, 0)
            """,
            (
                run_id,
                build_id,
                queue_id,
                provider,
                triggered_by,
                len(candidates),
                json.dumps(timeout_cfg),
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

    if queue_id:
        _set_queue_fields(
            queue_id,
            status="running",
            run_id=run_id,
            started_at=now,
            last_error=None,
        )

    submit_result = _submit_queued_tasks(run_id=run_id, limit=200)
    summary = _reconcile_run(run_id)
    summary["submission"] = submit_result
    return summary


def enqueue_fetch_build(
    build_id: str,
    *,
    requested_by: str = "manual",
    source: str = "build_fetch",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    build = rythmx_store.get_forge_build(build_id)
    if not build:
        raise ValueError("Build not found")

    now = _utcnow()
    payload_json = json.dumps(payload or {"build_id": build_id})
    with rythmx_store._connect() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM fetch_queue
            WHERE build_id = ?
              AND status IN ('pending', 'running')
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (build_id,),
        ).fetchone()
        if existing:
            queue = _queue_summary(str(existing["id"]))
            started = _start_next_pending_queue_item()
            return {
                "queue": queue,
                "existing": True,
                "started_run": started,
            }

        row = conn.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 AS next_pos FROM fetch_queue").fetchone()
        next_pos = int((row["next_pos"] if row else 1) or 1)
        queue_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO fetch_queue
                (id, build_id, source, payload_json, status, queue_position,
                 requested_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (queue_id, build_id, source, payload_json, next_pos, requested_by, now, now),
        )

    started = _start_next_pending_queue_item()
    queue = _queue_summary(queue_id)
    return {
        "queue": queue,
        "existing": False,
        "started_run": started,
    }


def list_fetch_queue(
    *,
    status: str | None = None,
    build_source: str | None = None,
    include_canceled: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("fq.status = ?")
        params.append(status)
    elif not include_canceled:
        where.append("fq.status != 'canceled'")
    if build_source:
        where.append("fb.source = ?")
        params.append(build_source)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT fq.*,
                   fb.source AS build_source,
                   fb.name AS build_name,
                   fb.status AS build_status,
                   fr.status AS run_status,
                   fr.handoff_status AS run_handoff_status
            FROM fetch_queue fq
            LEFT JOIN forge_builds fb ON fb.id = fq.build_id
            LEFT JOIN fetch_runs fr ON fr.id = fq.run_id
            {where_sql}
            ORDER BY fq.queue_position ASC, datetime(fq.created_at) ASC
            LIMIT ?
            """,
            tuple(params + [max(1, min(limit, 2000))]),
        ).fetchall()
    return [_queue_from_row(dict(r)) for r in rows]


def cancel_fetch_queue_item(queue_id: str, *, auto_advance: bool = True) -> dict[str, Any]:
    queue = _queue_summary(queue_id)
    if not queue:
        raise ValueError("Queue item not found")
    current_status = str(queue.get("status") or "")
    if current_status in {"completed", "failed", "canceled"}:
        return {"queue": queue, "canceled": False}

    now = _utcnow()
    _set_queue_fields(
        queue_id,
        status="canceled",
        finished_at=now,
        last_error="Canceled by user",
    )

    run_id = str(queue.get("run_id") or "").strip()
    if run_id:
        _set_run_fields(
            run_id,
            cancel_requested=1,
            last_error="Fetch run cancel requested",
        )

    if auto_advance:
        _start_next_pending_queue_item()
    return {"queue": _queue_summary(queue_id), "canceled": True}


def cancel_fetch_queue_batch(
    *,
    queue_ids: list[str] | None = None,
    status: str | None = None,
    build_source: str | None = None,
) -> dict[str, Any]:
    where = ["fq.status IN ('pending', 'running')"]
    params: list[Any] = []

    ids = [str(v).strip() for v in (queue_ids or []) if str(v).strip()]
    if ids:
        where.append("fq.id IN (" + ",".join(["?"] * len(ids)) + ")")
        params.extend(ids)
    if status:
        where.append("fq.status = ?")
        params.append(status)
    if build_source:
        where.append("fb.source = ?")
        params.append(build_source)

    with rythmx_store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT fq.id
            FROM fetch_queue fq
            LEFT JOIN forge_builds fb ON fb.id = fq.build_id
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()
    targets = [str(r["id"]) for r in rows]
    if not targets:
        return {"canceled": 0, "queue_ids": []}

    canceled_ids: list[str] = []
    for qid in targets:
        result = cancel_fetch_queue_item(qid, auto_advance=False)
        if result.get("canceled"):
            canceled_ids.append(qid)

    _start_next_pending_queue_item()

    return {"canceled": len(canceled_ids), "queue_ids": canceled_ids}


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
            "handoff_status": str(summary.get("handoff_status") or "idle"),
            "handoff_error": summary.get("handoff_error"),
        },
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


def delete_fetch_run(run_id: str, *, remove_queue_item: bool = True) -> dict[str, Any]:
    summary = _run_summary(run_id)
    if not summary:
        raise ValueError("Fetch run not found")
    if str(summary.get("status") or "").strip().lower() == "running":
        raise RuntimeError("Cannot delete a running fetch run")

    queue_id = str(summary.get("queue_id") or "").strip()
    with rythmx_store._connect() as conn:
        tasks_deleted = int(
            (conn.execute("DELETE FROM fetch_tasks WHERE run_id = ?", (run_id,)).rowcount or 0)
        )
        runs_deleted = int(
            (conn.execute("DELETE FROM fetch_runs WHERE id = ?", (run_id,)).rowcount or 0)
        )
        queue_deleted = 0
        if remove_queue_item:
            if queue_id:
                queue_deleted += int(
                    (conn.execute("DELETE FROM fetch_queue WHERE id = ?", (queue_id,)).rowcount or 0)
                )
            queue_deleted += int(
                (conn.execute("DELETE FROM fetch_queue WHERE run_id = ?", (run_id,)).rowcount or 0)
            )

    return {
        "deleted": bool(runs_deleted > 0),
        "run_id": run_id,
        "tasks_deleted": tasks_deleted,
        "queue_deleted": queue_deleted,
    }


def delete_fetch_runs_for_build(build_id: str, *, include_running: bool = False) -> dict[str, Any]:
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, status, queue_id
            FROM fetch_runs
            WHERE build_id = ?
            """,
            (build_id,),
        ).fetchall()

        run_ids: list[str] = []
        tasks_deleted = 0
        queue_deleted = 0
        skipped_running = 0

        for row in rows:
            run_id = str(row["id"])
            status = str(row["status"] or "").strip().lower()
            queue_id = str(row["queue_id"] or "").strip()
            if status == "running" and not include_running:
                skipped_running += 1
                continue
            tasks_deleted += int(
                (conn.execute("DELETE FROM fetch_tasks WHERE run_id = ?", (run_id,)).rowcount or 0)
            )
            queue_deleted += int(
                (conn.execute("DELETE FROM fetch_queue WHERE run_id = ?", (run_id,)).rowcount or 0)
            )
            if queue_id:
                queue_deleted += int(
                    (conn.execute("DELETE FROM fetch_queue WHERE id = ?", (queue_id,)).rowcount or 0)
                )
            deleted = int((conn.execute("DELETE FROM fetch_runs WHERE id = ?", (run_id,)).rowcount or 0))
            if deleted > 0:
                run_ids.append(run_id)

    return {
        "deleted": len(run_ids),
        "run_ids": run_ids,
        "tasks_deleted": tasks_deleted,
        "queue_deleted": queue_deleted,
        "skipped_running": skipped_running,
    }


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


def set_manual_release_for_task(run_id: str, task_id: int, tidal_album_id: str) -> dict[str, Any]:
    tidal_album_id = str(tidal_album_id or "").strip()
    if not tidal_album_id.isdigit():
        raise ValueError("tidal_album_id must be numeric")

    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM fetch_tasks
            WHERE id = ? AND run_id = ?
            LIMIT 1
            """,
            (int(task_id), run_id),
        ).fetchone()
        if not row:
            raise ValueError("Fetch task not found")
        task = dict(row)
        metadata = _safe_json(task.get("metadata_json"), {})
        metadata["manual_tidal_album_id"] = tidal_album_id
        metadata["tidal_album_id"] = metadata.get("tidal_album_id") or tidal_album_id
        now = _utcnow()
        conn.execute(
            """
            UPDATE fetch_tasks
            SET metadata_json = ?,
                match_status = 'queued',
                match_strategy = 'manual_id',
                match_confidence = NULL,
                match_reasons_json = '[]',
                match_candidates_json = '[]',
                updated_at = ?,
                last_transition_at = ?
            WHERE id = ?
            """,
            (json.dumps(metadata, ensure_ascii=True), now, now, int(task_id)),
        )

    refreshed = list_fetch_tasks_for_run(run_id, limit=10000)
    for item in refreshed:
        if int(item["id"]) == int(task_id):
            return item
    raise ValueError("Fetch task not found")


def probe_fetch_match(
    *,
    build_id: str | None = None,
    run_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    if not build_id and not run_id:
        raise ValueError("build_id or run_id is required")

    downloader = _plugins.get_downloader()
    preview = getattr(downloader, "preview_match", None)
    if not callable(preview):
        raise RuntimeError(f"Downloader '{getattr(downloader, 'name', 'unknown')}' does not support preview_match")

    work_items: list[dict[str, Any]] = []
    if run_id:
        run = get_fetch_run(run_id)
        if not run:
            raise ValueError("Fetch run not found")
        for task in list_fetch_tasks_for_run(run_id, limit=max(1, min(limit, 5000))):
            work_items.append(
                {
                    "task_id": int(task["id"]),
                    "artist_name": str(task.get("artist_name") or ""),
                    "album_name": str(task.get("album_name") or ""),
                    "metadata": dict(task.get("metadata") or {}),
                }
            )
    else:
        build = rythmx_store.get_forge_build(str(build_id))
        if not build:
            raise ValueError("Build not found")
        for idx, item in enumerate(_build_fetch_candidates(build)):
            work_items.append(
                {
                    "task_id": None,
                    "artist_name": str(item.get("artist_name") or ""),
                    "album_name": str(item.get("album_name") or ""),
                    "metadata": dict(item.get("metadata") or {}),
                    "candidate_index": idx,
                }
            )
        work_items = work_items[: max(1, min(limit, 5000))]

    counts = {"confident": 0, "ambiguous": 0, "unresolved": 0, "search_inconsistent": 0}
    rows: list[dict[str, Any]] = []
    for item in work_items:
        artist = str(item.get("artist_name") or "")
        album = str(item.get("album_name") or "")
        metadata = dict(item.get("metadata") or {})
        try:
            result = preview(artist, album, metadata)
            if not isinstance(result, dict):
                result = {}
        except Exception as exc:
            result = {
                "match_status": "unresolved",
                "match_strategy": "probe_error",
                "match_confidence": 0.0,
                "match_reasons": [str(exc)],
                "candidates": [],
            }

        match_status = str(result.get("match_status") or result.get("status") or "unresolved")
        if match_status not in counts:
            match_status = "unresolved"
        counts[match_status] += 1

        rows.append(
            {
                "task_id": item.get("task_id"),
                "artist_name": artist,
                "album_name": album,
                "match_status": match_status,
                "match_strategy": str(result.get("match_strategy") or ""),
                "match_confidence": float(result.get("match_confidence") or 0.0),
                "match_reasons": list(result.get("match_reasons") or []),
                "candidates": list(result.get("candidates") or []),
                "selected": result.get("selected"),
            }
        )

    return {
        "build_id": build_id,
        "run_id": run_id,
        "provider": str(getattr(downloader, "name", "unknown")),
        "counts": counts,
        "total": len(rows),
        "items": rows,
    }


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
                    match_status = NULL,
                    match_strategy = NULL,
                    match_confidence = NULL,
                    match_reasons_json = '[]',
                    match_candidates_json = '[]',
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
                    handoff_status = 'idle',
                    handoff_started_at = NULL,
                    handoff_finished_at = NULL,
                    handoff_error = NULL,
                    terminal_emitted = 0,
                    cancel_requested = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, run_id),
            )

            queue_id = str(summary.get("queue_id") or "").strip()
            if queue_id:
                conn.execute(
                    """
                    UPDATE fetch_queue
                    SET status = 'running',
                        finished_at = NULL,
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, queue_id),
                )

    submission = _submit_queued_tasks(run_id=run_id, limit=500)
    refreshed = _reconcile_run(run_id)
    return {
        "run": refreshed,
        "retried": retried,
        "submission": submission,
    }


def poll_once() -> dict[str, Any]:
    started = _start_next_pending_queue_item()

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
    running_run_ids = [str(row["id"]) for row in run_rows]

    canceled = 0
    for run_id in running_run_ids:
        canceled += _process_run_cancel_requests(run_id).get("canceled_tasks", 0)

    submitted = _submit_queued_tasks(limit=500)
    provider = _poll_provider_updates(limit=1000)
    downloaded = _process_downloaded_tasks(limit=500)
    scan_requested = _advance_moved_to_scan_requested(limit=500)

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
    handoff = {"in_library": 0, "timed_out": 0, "waiting": 0}
    for row in run_rows:
        run_id = str(row["id"])
        handoff_result = _process_run_handoff(run_id)
        handoff["in_library"] += int(handoff_result.get("in_library", 0))
        handoff["timed_out"] += int(handoff_result.get("timed_out", 0))
        handoff["waiting"] += int(handoff_result.get("waiting", 0))
        run = _reconcile_run(run_id)
        if run:
            reconciled.append(run)
            if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
                _sync_queue_for_terminal_run(run)

    next_started = _start_next_pending_queue_item()

    return {
        "checked": len(run_rows),
        "started": bool(started),
        "next_started": bool(next_started),
        "canceled": canceled,
        "submitted": submitted,
        "provider": provider,
        "downloaded": downloaded,
        "scan_requested": scan_requested,
        "scan": handoff,
        "runs": reconciled,
    }
