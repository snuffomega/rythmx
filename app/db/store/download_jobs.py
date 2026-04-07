"""
Download jobs store — tracks plugin-based fetch jobs initiated from Forge builds.

Separate from download_queue (CC pipeline acquisition queue).
Each row = one plugin submit() call for one unique album in a build.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

import sqlite3

_ALLOWED_STATUSES = {"pending", "completed", "failed"}


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def insert_job(
    connect: Callable[[], sqlite3.Connection],
    *,
    build_id: str,
    job_id: str,
    provider: str,
    artist_name: str,
    album_name: str,
) -> int:
    """Insert a new download job. Returns the new row id, or -1 on duplicate."""
    now = _utcnow()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO download_jobs
                (build_id, job_id, provider, artist_name, album_name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (build_id, job_id, provider, artist_name, album_name, now, now),
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM download_jobs WHERE build_id = ? AND job_id = ?",
            (build_id, job_id),
        ).fetchone()
        return row["id"] if row else -1


def get_jobs_for_build(
    connect: Callable[[], sqlite3.Connection],
    build_id: str,
) -> list[dict]:
    """Return all jobs for a build, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM download_jobs WHERE build_id = ? ORDER BY created_at",
            (build_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_jobs(
    connect: Callable[[], sqlite3.Connection],
    provider: str | None = None,
) -> list[dict]:
    """Return all pending jobs for the completion poller, optionally filtered by provider."""
    with connect() as conn:
        if provider:
            rows = conn.execute(
                "SELECT * FROM download_jobs WHERE status = 'pending' AND provider = ? ORDER BY created_at",
                (provider,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM download_jobs WHERE status = 'pending' ORDER BY created_at",
            ).fetchall()
    return [dict(r) for r in rows]


def update_job_status(
    connect: Callable[[], sqlite3.Connection],
    job_id: str,
    status: str,
    storage_path: str | None = None,
) -> bool:
    """Update a job's status (and optionally storage_path). Returns True on success."""
    if status not in _ALLOWED_STATUSES:
        return False
    now = _utcnow()
    with connect() as conn:
        params: list = [status, now]
        extra = ""
        if storage_path is not None:
            extra = ", storage_path = ?"
            params.append(storage_path)
        params.append(job_id)
        cur = conn.execute(
            f"UPDATE download_jobs SET status = ?, updated_at = ?{extra} WHERE job_id = ?",
            params,
        )
        return (cur.rowcount or 0) > 0
