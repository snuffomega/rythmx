"""
Forge build persistence helpers for rythmx.db.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable

import sqlite3

ALLOWED_BUILD_SOURCES = {"new_music", "custom_discovery", "sync", "manual"}
ALLOWED_BUILD_STATUSES = {"queued", "building", "ready", "published", "failed"}


def _normalize_source(source: str | None) -> str:
    normalized = (source or "manual").strip().lower()
    return normalized if normalized in ALLOWED_BUILD_SOURCES else "manual"


def _normalize_status(status: str | None) -> str:
    normalized = (status or "ready").strip().lower()
    return normalized if normalized in ALLOWED_BUILD_STATUSES else "ready"


def _safe_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except Exception:
        return fallback


def _shape_row(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    data = dict(row)
    track_list = _safe_json(data.get("track_list_json"), [])
    summary = _safe_json(data.get("summary_json"), {})
    data["track_list"] = track_list
    data["summary"] = summary
    data["item_count"] = len(track_list)
    data.pop("track_list_json", None)
    data.pop("summary_json", None)
    return data


def create_forge_build(
    connect: Callable[[], sqlite3.Connection],
    name: str,
    source: str = "manual",
    status: str = "ready",
    track_list: list[dict] | list[Any] | None = None,
    summary: dict | None = None,
    run_mode: str | None = None,
    build_id: str | None = None,
) -> dict:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    bid = build_id or str(uuid.uuid4())
    safe_name = (name or "").strip() or f"Build {now}"
    safe_source = _normalize_source(source)
    safe_status = _normalize_status(status)
    track_json = json.dumps(track_list or [])
    summary_json = json.dumps(summary or {})

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO forge_builds
                (id, name, source, status, run_mode, track_list_json, summary_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bid, safe_name, safe_source, safe_status, run_mode, track_json, summary_json, now, now),
        )
        row = conn.execute("SELECT * FROM forge_builds WHERE id = ?", (bid,)).fetchone()
    return _shape_row(row) or {}


def list_forge_builds(
    connect: Callable[[], sqlite3.Connection],
    source: str | None = None,
    limit: int = 100,
) -> list[dict]:
    with connect() as conn:
        if source:
            rows = conn.execute(
                """
                SELECT * FROM forge_builds
                WHERE source = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (_normalize_source(source), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM forge_builds
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_shape_row(row) for row in rows if row]  # type: ignore[arg-type]


def get_forge_build(
    connect: Callable[[], sqlite3.Connection],
    build_id: str,
) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM forge_builds WHERE id = ?", (build_id,)).fetchone()
    return _shape_row(row)


def delete_forge_build(
    connect: Callable[[], sqlite3.Connection],
    build_id: str,
) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM forge_builds WHERE id = ?", (build_id,))
        return (cur.rowcount or 0) > 0


def update_forge_build_status(
    connect: Callable[[], sqlite3.Connection],
    build_id: str,
    status: str,
) -> bool:
    safe_status = _normalize_status(status)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE forge_builds
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (safe_status, now, build_id),
        )
        return (cur.rowcount or 0) > 0
