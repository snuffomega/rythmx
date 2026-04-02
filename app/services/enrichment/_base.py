"""
_base.py — Shared enrichment worker loop + enrichment_meta writer.

Eliminates the 12-step boilerplate that was duplicated across 8 workers.
Workers import run_enrichment_loop() and supply only what varies:
  - candidate_sql / remaining_sql
  - source / entity_type
  - process_item(conn, row) -> "found" | "not_found"
"""
import logging
import threading

from app.db.rythmx_store import _connect

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# enrichment_meta writer (single source of truth — was _write_enrichment_meta)
# ---------------------------------------------------------------------------

def write_enrichment_meta(conn, source: str, entity_type: str, entity_id: str,
                          status: str, error_msg: str | None = None,
                          confidence: int | None = None) -> None:
    """Upsert a row into enrichment_meta.  Silently ignores if table doesn't exist yet.
    For 'not_found' status, automatically sets retry_after = date('now', '+30 days')."""
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO enrichment_meta
                (source, entity_type, entity_id, status, enriched_at, error_msg, confidence,
                 retry_after)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?,
                    CASE WHEN ? = 'not_found' THEN date('now', '+30 days') ELSE NULL END)
            """,
            (source, entity_type, entity_id, status, error_msg, confidence, status),
        )
    except Exception as e:
        logger.debug("enrichment_meta write skipped: %s", e)


# ---------------------------------------------------------------------------
# Generic enrichment loop
# ---------------------------------------------------------------------------

def run_enrichment_loop(
    *,
    worker_name: str,
    candidate_sql: str,
    candidate_params: tuple = (),
    remaining_sql: str,
    remaining_params: tuple = (),
    source: str,
    entity_type: str,
    entity_id_col: str = "id",
    process_item: "callable",
    batch_size: int = 50,
    stop_event: threading.Event | None = None,
    on_progress: "callable | None" = None,
) -> dict:
    """
    Generic enrichment worker loop.

    ``process_item(conn, row)`` must:
      - Make API calls + write to lib_* tables
      - Call ``write_enrichment_meta(...)`` for the entity
      - Return ``"found"`` or ``"not_found"``
      - Raise ``Exception`` on failure (the loop writes an error meta row)

    Returns ``{enriched, skipped, failed, remaining}``.
    """
    enriched = 0
    skipped = 0
    failed = 0

    # ---- 1. Load candidates ----
    try:
        with _connect() as conn:
            rows = conn.execute(
                candidate_sql + " LIMIT ?",
                (*candidate_params, batch_size),
            ).fetchall()
    except Exception as e:
        logger.error("%s: could not load candidates: %s", worker_name, e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    # ---- 2. Per-item processing ----
    try:
        conn = _connect()
    except Exception as e:
        logger.error("%s: could not open worker DB connection: %s", worker_name, e)
        return {
            "enriched": 0,
            "skipped": 0,
            "failed": len(rows),
            "remaining": -1,
            "error": str(e),
        }

    try:
        for row in rows:
            if stop_event and stop_event.is_set():
                break

            entity_id = row[entity_id_col]

            try:
                result = process_item(conn, row)
                if result == "found":
                    enriched += 1
                else:
                    skipped += 1
                conn.commit()
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
            except Exception as e:
                # Ensure partial writes from process_item do not leak into the next item.
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("%s: failed for '%s': %s", worker_name, entity_id, e)
                write_enrichment_meta(
                    conn,
                    source,
                    entity_type,
                    str(entity_id),
                    "error",
                    error_msg=str(e)[:200],
                )
                try:
                    conn.commit()
                except Exception:
                    pass
                failed += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # ---- 3. Count remaining ----
    try:
        with _connect() as conn:
            remaining = conn.execute(remaining_sql, remaining_params).fetchone()[0]
    except Exception:
        remaining = -1

    logger.info("%s: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                worker_name, enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}
