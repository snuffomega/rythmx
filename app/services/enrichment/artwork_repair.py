"""
artwork_repair.py - Repair stale image_cache hashes whose source blobs are missing.

When image_cache.content_hash exists but the corresponding file is absent from
ARTWORK_DIR/originals, local artwork URLs will 404 forever. This module clears
those stale hashes so the normal enrichment stages can refill them.
"""
from __future__ import annotations

import logging

from app.db.rythmx_store import _connect
from app.services.artwork_store import get_original_path

logger = logging.getLogger(__name__)


def reset_missing_content_hashes(
    entity_types: tuple[str, ...] = ("album", "artist"),
    limit: int = 500,
) -> dict:
    """
    Clear stale content_hash/local_path rows for the requested image_cache entity types.

    Args:
      entity_types: which entity types to scan (album, artist).
      limit: cap on rows scanned per call. 0 = unlimited (use only for manual/admin runs).
             Default 500 keeps startup overhead sub-second on large libraries.
             Rows are ordered by last_accessed ASC so oldest-stale entries are repaired first.

    Returns:
      {
        "scanned": int,
        "reset": int,
        "scanned_by_type": {"album": int, "artist": int},
        "reset_by_type": {"album": int, "artist": int},
      }

    Note: This function is intentionally capped for startup use. Full library repair
    should be triggered via the scheduled maintenance task (future: POST /maintenance/artwork-repair).
    """
    requested = tuple(t for t in entity_types if t in ("album", "artist"))
    if not requested:
        return {"scanned": 0, "reset": 0, "scanned_by_type": {}, "reset_by_type": {}}

    placeholders = ",".join("?" for _ in requested)
    scanned_by_type = {t: 0 for t in requested}
    reset_by_type = {t: 0 for t in requested}
    stale_rows: list[tuple[str, str]] = []

    limit_clause = f"LIMIT {limit}" if limit > 0 else ""

    with _connect() as conn:
        # Safety guard: skip entirely if image_cache is empty (e.g. pre-first-sync).
        row_count = conn.execute(
            "SELECT COUNT(*) FROM image_cache WHERE entity_type IN ({}) AND content_hash IS NOT NULL AND content_hash != ''".format(placeholders),
            requested,
        ).fetchone()[0]
        if row_count == 0:
            return {"scanned": 0, "reset": 0, "scanned_by_type": scanned_by_type, "reset_by_type": reset_by_type}

        rows = conn.execute(
            f"""
            SELECT entity_type, entity_key, content_hash
            FROM image_cache
            WHERE entity_type IN ({placeholders})
              AND content_hash IS NOT NULL
              AND content_hash != ''
            ORDER BY last_accessed ASC
            {limit_clause}
            """,
            requested,
        ).fetchall()

        for row in rows:
            entity_type = str(row["entity_type"])
            entity_key = str(row["entity_key"])
            content_hash = str(row["content_hash"] or "")
            scanned_by_type[entity_type] = scanned_by_type.get(entity_type, 0) + 1
            try:
                if not get_original_path(content_hash).exists():
                    stale_rows.append((entity_type, entity_key))
                    reset_by_type[entity_type] = reset_by_type.get(entity_type, 0) + 1
            except Exception:
                # Invalid hash format or path resolution issue - treat as stale.
                stale_rows.append((entity_type, entity_key))
                reset_by_type[entity_type] = reset_by_type.get(entity_type, 0) + 1

        if stale_rows:
            conn.executemany(
                """
                UPDATE image_cache
                   SET content_hash = NULL,
                       local_path = NULL,
                       last_accessed = datetime('now')
                 WHERE entity_type = ?
                   AND entity_key = ?
                """,
                stale_rows,
            )

    scanned = sum(scanned_by_type.values())
    reset = sum(reset_by_type.values())
    if reset:
        logger.info(
            "artwork_repair: scanned=%d reset=%d (album=%d, artist=%d)",
            scanned,
            reset,
            reset_by_type.get("album", 0),
            reset_by_type.get("artist", 0),
        )
    return {
        "scanned": scanned,
        "reset": reset,
        "scanned_by_type": scanned_by_type,
        "reset_by_type": reset_by_type,
    }

