"""
Pipeline history helpers for rythmx.db.
"""
from __future__ import annotations

import json as _json
from typing import Callable

import sqlite3


def insert_pipeline_run(
    connect: Callable[[], sqlite3.Connection],
    pipeline_type: str,
    run_mode: str,
    config_snapshot: dict,
    triggered_by: str = "manual",
) -> int:
    """Insert a new pipeline_history row at run start. Returns the new row id."""
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO pipeline_history
               (pipeline_type, run_mode, status, config_json, triggered_by)
               VALUES (?, ?, 'running', ?, ?)""",
            (pipeline_type, run_mode, _json.dumps(config_snapshot), triggered_by),
        )
        return cur.lastrowid


def complete_pipeline_run(
    connect: Callable[[], sqlite3.Connection],
    run_id: int,
    summary: dict,
    error_message: str | None = None,
) -> None:
    """Mark a pipeline_history row as completed (or error) with duration and summary."""
    status = "error" if error_message else "completed"
    with connect() as conn:
        conn.execute(
            """UPDATE pipeline_history
               SET status = ?,
                   finished_at = CURRENT_TIMESTAMP,
                   run_duration = (julianday(CURRENT_TIMESTAMP)
                                   - julianday(started_at)) * 86400,
                   summary_json = ?,
                   error_message = ?
               WHERE id = ?""",
            (status, _json.dumps(summary), error_message, run_id),
        )


def get_pipeline_runs(
    connect: Callable[[], sqlite3.Connection],
    pipeline_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent pipeline_history rows, optionally filtered by pipeline_type."""
    with connect() as conn:
        if pipeline_type:
            rows = conn.execute(
                """SELECT * FROM pipeline_history
                   WHERE pipeline_type = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (pipeline_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM pipeline_history
                   ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

