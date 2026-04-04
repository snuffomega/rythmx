"""
audit.py - Library audit routes for low-confidence / unverified matches.

Extracted from library_browse.py to reduce route-module sprawl while keeping
all API paths stable.
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key
from app.services.enrichment._helpers import match_album_title, strip_title_suffixes

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

_ALLOWED_SOURCES = {"itunes", "deezer"}


def _ensure_match_override_tables(conn) -> None:
    """
    Defensive table creation for deployments that have not restarted into the
    latest migration yet. Migration remains the source of truth.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_overrides (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            source TEXT NOT NULL,
            confirmed_id TEXT,
            state TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            updated_by TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_type, entity_id, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_override_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            candidate_id TEXT,
            note TEXT,
            actor TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _upsert_manual_meta(
    conn,
    *,
    source: str,
    entity_type: str,
    entity_id: str,
    status: str,
    confidence: int,
    manual_tag: str,
) -> None:
    conn.execute(
        """
        INSERT INTO enrichment_meta
            (source, entity_type, entity_id, status, enriched_at, error_msg,
             confidence, retry_after, verified_at)
        VALUES (
            ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?,
            CASE WHEN ? = 'not_found' THEN date('now', '+30 days') ELSE NULL END,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT(source, entity_type, entity_id) DO UPDATE SET
            status = excluded.status,
            enriched_at = excluded.enriched_at,
            error_msg = excluded.error_msg,
            confidence = excluded.confidence,
            retry_after = excluded.retry_after,
            verified_at = excluded.verified_at
        """,
        (source, entity_type, entity_id, status, manual_tag, confidence, status),
    )


def _candidate_reasons(
    *,
    score: float,
    candidate_title: str,
    requested_title: str,
    candidate_track_count: int | None,
    album_track_count: int,
) -> list[str]:
    reasons: list[str] = []
    cand_norm = strip_title_suffixes(candidate_title).strip().lower()
    req_norm = strip_title_suffixes(requested_title).strip().lower()

    if cand_norm == req_norm:
        reasons.append("title_exact")
    elif score >= 0.95:
        reasons.append("title_near_exact")
    elif score >= 0.82:
        reasons.append("title_overlap")

    if album_track_count > 0 and candidate_track_count and candidate_track_count > 0:
        diff = abs(album_track_count - candidate_track_count)
        if diff == 0:
            reasons.append("track_count_exact")
        elif diff <= 1:
            reasons.append("track_count_close")

    return reasons


@router.get("/library/audit/candidates")
def library_audit_candidates(
    album_id: str = Query(default="", min_length=1),
    source: str = Query(default="itunes"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Return ranked catalog candidates for a single album/source pair.

    Read-only evidence endpoint for manual Fix Match UX.
    """
    src = source.strip().lower()
    if src not in ("itunes", "deezer"):
        return JSONResponse(
            {"status": "error", "message": "source must be one of: itunes, deezer"},
            status_code=400,
        )

    with rythmx_store._connect() as conn:
        album_row = conn.execute(
            """
            SELECT la.id,
                   la.artist_id,
                   la.title,
                   la.itunes_album_id,
                   la.deezer_id,
                   la.match_confidence,
                   la.needs_verification,
                   ar.name AS artist_name
            FROM lib_albums la
            JOIN lib_artists ar ON ar.id = la.artist_id
            WHERE la.id = ?
              AND la.removed_at IS NULL
            LIMIT 1
            """,
            (album_id,),
        ).fetchone()

        if not album_row:
            return JSONResponse(
                {"status": "error", "message": "Album not found"},
                status_code=404,
            )

        album_track_count_row = conn.execute(
            "SELECT COUNT(*) FROM lib_tracks WHERE album_id = ? AND removed_at IS NULL",
            (album_id,),
        ).fetchone()
        album_track_count = int(album_track_count_row[0] or 0) if album_track_count_row else 0

        rows = conn.execute(
            """
            SELECT album_id, album_title, record_type, track_count, artwork_url
            FROM lib_artist_catalog
            WHERE artist_id = ?
              AND source = ?
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (album_row["artist_id"], src, max(limit * 4, 20)),
        ).fetchall()

    requested_title = str(album_row["title"] or "")
    scored: list[dict[str, Any]] = []
    for row in rows:
        candidate_title = str(row["album_title"] or "")
        score = float(match_album_title(requested_title, candidate_title))
        scored.append(
            {
                "candidate_id": str(row["album_id"] or ""),
                "candidate_title": candidate_title,
                "candidate_score": round(score, 4),
                "record_type": row["record_type"],
                "track_count": row["track_count"],
                "artwork_url": row["artwork_url"],
                "reasons": _candidate_reasons(
                    score=score,
                    candidate_title=candidate_title,
                    requested_title=requested_title,
                    candidate_track_count=int(row["track_count"] or 0) if row["track_count"] is not None else None,
                    album_track_count=album_track_count,
                ),
            }
        )

    def _rank(item: dict[str, Any]) -> tuple[float, int, str]:
        tc = item.get("track_count")
        tc_diff = abs(int(tc) - album_track_count) if tc is not None and album_track_count > 0 else 999
        return (float(item.get("candidate_score") or 0.0), -tc_diff, str(item.get("candidate_title") or ""))

    top = sorted(scored, key=_rank, reverse=True)[:limit]

    return {
        "status": "ok",
        "album": {
            "id": album_row["id"],
            "artist_id": album_row["artist_id"],
            "artist_name": album_row["artist_name"],
            "title": album_row["title"],
            "itunes_album_id": album_row["itunes_album_id"],
            "deezer_id": album_row["deezer_id"],
            "match_confidence": album_row["match_confidence"],
            "needs_verification": bool(album_row["needs_verification"]),
            "track_count": album_track_count,
        },
        "source": src,
        "candidates": top,
    }


@router.get("/library/audit")
def library_audit(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
):
    """
    Return artists and albums with needs_verification=1 or match_confidence < 85.
    Groups by artist. Includes per-source enrichment_meta confidence scores.
    """
    offset = (page - 1) * per_page

    with rythmx_store._connect() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(DISTINCT ar.id)
            FROM lib_artists ar
            JOIN lib_albums la ON la.artist_id = ar.id
            WHERE la.removed_at IS NULL
              AND (la.needs_verification = 1 OR la.match_confidence < 85)
            """
        ).fetchone()
        total = total_row[0] if total_row else 0

        rows = conn.execute(
            """
            SELECT ar.id AS artist_id, ar.name AS artist_name,
                   la.id AS album_id, la.title AS album_title, la.year AS album_year,
                   la.match_confidence, la.needs_verification,
                   la.itunes_album_id, la.deezer_id
            FROM lib_artists ar
            JOIN lib_albums la ON la.artist_id = ar.id
            WHERE la.removed_at IS NULL
              AND (la.needs_verification = 1 OR la.match_confidence < 85)
            ORDER BY ar.name COLLATE NOCASE, la.title COLLATE NOCASE
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()

        album_ids = [r["album_id"] for r in rows]
        meta_map: dict[str, dict] = {}
        manual_map: dict[str, dict[str, dict[str, Any]]] = {}
        if album_ids:
            placeholders = ",".join("?" * len(album_ids))
            meta_rows = conn.execute(
                f"""
                SELECT entity_id, source, status, confidence
                FROM enrichment_meta
                WHERE entity_type = 'album' AND entity_id IN ({placeholders})
                """,
                album_ids,
            ).fetchall()
            for m in meta_rows:
                meta_map.setdefault(m["entity_id"], {})[m["source"]] = {
                    "status": m["status"],
                    "confidence": m["confidence"],
                }
            # match_overrides may not exist on pre-migration DBs; degrade gracefully.
            try:
                mo_rows = conn.execute(
                    f"""
                    SELECT entity_id, source, state, locked, confirmed_id
                    FROM match_overrides
                    WHERE entity_type = 'album'
                      AND entity_id IN ({placeholders})
                    """,
                    album_ids,
                ).fetchall()
                for mo in mo_rows:
                    manual_map.setdefault(mo["entity_id"], {})[mo["source"]] = {
                        "state": mo["state"],
                        "locked": bool(mo["locked"]),
                        "confirmed_id": mo["confirmed_id"],
                    }
            except Exception:
                manual_map = {}

    items = []
    for r in rows:
        items.append({
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "album_id": r["album_id"],
            "album_title": r["album_title"],
            "album_year": r["album_year"],
            "match_confidence": r["match_confidence"],
            "needs_verification": bool(r["needs_verification"]),
            "itunes_album_id": r["itunes_album_id"],
            "deezer_id": r["deezer_id"],
            "enrichment": meta_map.get(r["album_id"], {}),
            "manual_overrides": manual_map.get(r["album_id"], {}),
        })

    return {"status": "ok", "items": items, "total": total, "page": page}


@router.get("/library/audit/artwork")
def library_artwork_audit(
    limit: int = Query(default=100, ge=1, le=500),
):
    """
    Artwork precedence audit surface (Phase 35a).

    Highlights albums where runtime display may rely on name-key resolver cache
    instead of library-owned hash/source fields, with extra focus on
    low-confidence or verification-needed matches.
    """
    with rythmx_store._connect() as conn:
        summary_row = conn.execute(
            """
            WITH album_state AS (
                SELECT la.id,
                       la.match_confidence,
                       la.needs_verification,
                       COALESCE(ia.content_hash, '') AS id_hash,
                       COALESCE(ia.image_url, '') AS id_image_url,
                       COALESCE(la.thumb_url_deezer, '') AS deezer_url,
                       COALESCE(la.thumb_url_plex, '') AS plex_url,
                       COALESCE(rk.image_url, '') AS resolver_url
                FROM lib_albums la
                JOIN lib_artists ar ON ar.id = la.artist_id
                LEFT JOIN image_cache ia
                       ON ia.entity_type = 'album' AND ia.entity_key = la.id
                LEFT JOIN image_cache rk
                       ON rk.entity_type = 'album'
                      AND rk.entity_key = lower(ar.name) || '|||' || lower(la.title)
                WHERE la.removed_at IS NULL
            )
            SELECT COUNT(*) AS total_albums,
                   SUM(CASE WHEN id_hash != '' THEN 1 ELSE 0 END) AS with_local_hash,
                   SUM(CASE WHEN id_image_url != '' OR deezer_url != '' OR plex_url != '' THEN 1 ELSE 0 END) AS with_direct_source,
                   SUM(CASE WHEN id_hash = '' AND deezer_url = '' AND plex_url = '' AND resolver_url != '' THEN 1 ELSE 0 END) AS resolver_only,
                   SUM(CASE
                           WHEN id_hash = '' AND deezer_url = '' AND plex_url = '' AND resolver_url != ''
                            AND (COALESCE(match_confidence, 0) < 85 OR COALESCE(needs_verification, 0) = 1)
                           THEN 1 ELSE 0
                       END) AS resolver_only_low_conf
            FROM album_state
            """
        ).fetchone()

        rows = conn.execute(
            """
            SELECT la.id AS album_id,
                   la.title AS album_title,
                   ar.id AS artist_id,
                   ar.name AS artist_name,
                   la.match_confidence,
                   la.needs_verification,
                   la.itunes_album_id,
                   la.deezer_id,
                   ia.content_hash AS album_hash,
                   ia.image_url AS album_image_url,
                   ia.artwork_source,
                   la.thumb_url_deezer,
                   la.thumb_url_plex,
                   rk.image_url AS resolver_image_url,
                   rk.last_accessed AS resolver_last_accessed
            FROM lib_albums la
            JOIN lib_artists ar ON ar.id = la.artist_id
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'album' AND ia.entity_key = la.id
            LEFT JOIN image_cache rk
                   ON rk.entity_type = 'album'
                  AND rk.entity_key = lower(ar.name) || '|||' || lower(la.title)
            WHERE la.removed_at IS NULL
              AND COALESCE(rk.image_url, '') != ''
              AND (
                  (COALESCE(ia.content_hash, '') = ''
                   AND COALESCE(la.thumb_url_deezer, '') = ''
                   AND COALESCE(la.thumb_url_plex, '') = '')
                  OR COALESCE(la.match_confidence, 0) < 85
                  OR COALESCE(la.needs_verification, 0) = 1
              )
            ORDER BY COALESCE(la.match_confidence, 0) ASC,
                     ar.name COLLATE NOCASE,
                     la.title COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "status": "ok",
        "summary": dict(summary_row or {}),
        "items": [dict(r) for r in rows],
        "limit": limit,
    }


@router.post("/library/audit/confirm")
def library_audit_confirm(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Manually confirm an enrichment match.
    Body: { entity_type, entity_id, source, confirmed_id, note?, actor? }
    Sets needs_verification=0, match_confidence=100, writes the confirmed ID.
    """
    data = data or {}
    entity_type = str(data.get("entity_type", "")).strip()
    entity_id = str(data.get("entity_id", "")).strip()
    source = str(data.get("source", "")).strip().lower()
    confirmed_id = str(data.get("confirmed_id", "")).strip()[:200]
    note = str(data.get("note", "")).strip()[:1000] or None
    actor = str(data.get("actor", "")).strip()[:200] or None

    if not entity_type or not entity_id or not source or not confirmed_id:
        return JSONResponse(
            {"status": "error",
             "message": "entity_type, entity_id, source, confirmed_id required"},
            status_code=400,
        )
    if source not in _ALLOWED_SOURCES:
        return JSONResponse(
            {"status": "error", "message": "source must be one of: itunes, deezer"},
            status_code=400,
        )

    id_col_map = {
        "itunes": "itunes_album_id",
        "deezer": "deezer_id",
    }
    id_col = id_col_map.get(source) if entity_type == "album" else None

    try:
        with rythmx_store._connect() as conn:
            _ensure_match_override_tables(conn)
            if id_col and entity_type == "album":
                conn.execute(
                    f"""
                    UPDATE lib_albums
                    SET {id_col} = ?,
                        match_confidence = 100,
                        needs_verification = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (confirmed_id, entity_id),
                )
            conn.execute(
                """
                INSERT INTO match_overrides
                    (entity_type, entity_id, source, confirmed_id, state, locked, note, updated_by, updated_at)
                VALUES (?, ?, ?, ?, 'confirmed', 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id, source) DO UPDATE SET
                    confirmed_id = excluded.confirmed_id,
                    state = 'confirmed',
                    locked = 1,
                    note = excluded.note,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (entity_type, entity_id, source, confirmed_id, note, actor),
            )
            conn.execute(
                """
                INSERT INTO match_override_events
                    (entity_type, entity_id, source, action, candidate_id, note, actor)
                VALUES (?, ?, ?, 'confirm', ?, ?, ?)
                """,
                (entity_type, entity_id, source, confirmed_id, note, actor),
            )
            _upsert_manual_meta(
                conn,
                source=source,
                entity_type=entity_type,
                entity_id=entity_id,
                status="found",
                confidence=100,
                manual_tag="manual_confirm",
            )
    except Exception as e:
        logger.error("library_audit_confirm: DB write failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return {"status": "ok"}


@router.post("/library/audit/reject")
def library_audit_reject(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Reject an incorrect enrichment match.
    Body: { entity_type, entity_id, source, candidate_id?, note?, actor? }
    Clears the ID column, sets needs_verification=1.
    match_confidence is reset to 0 only when both source IDs are NULL.
    """
    data = data or {}
    entity_type = str(data.get("entity_type", "")).strip()
    entity_id = str(data.get("entity_id", "")).strip()
    source = str(data.get("source", "")).strip().lower()
    candidate_id = str(data.get("candidate_id", "")).strip()[:200] or None
    note = str(data.get("note", "")).strip()[:1000] or None
    actor = str(data.get("actor", "")).strip()[:200] or None

    if not entity_type or not entity_id or not source:
        return JSONResponse(
            {"status": "error", "message": "entity_type, entity_id, source required"},
            status_code=400,
        )
    if source not in _ALLOWED_SOURCES:
        return JSONResponse(
            {"status": "error", "message": "source must be one of: itunes, deezer"},
            status_code=400,
        )

    id_col_map = {
        "itunes": "itunes_album_id",
        "deezer": "deezer_id",
    }
    id_col = id_col_map.get(source) if entity_type == "album" else None

    try:
        with rythmx_store._connect() as conn:
            _ensure_match_override_tables(conn)
            if id_col and entity_type == "album":
                conn.execute(
                    f"""
                    UPDATE lib_albums
                    SET {id_col} = NULL,
                        match_confidence = CASE
                            WHEN {('deezer_id' if source == 'itunes' else 'itunes_album_id')} IS NULL THEN 0
                            ELSE match_confidence
                        END,
                        needs_verification = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (entity_id,),
                )
            conn.execute(
                """
                INSERT INTO match_overrides
                    (entity_type, entity_id, source, confirmed_id, state, locked, note, updated_by, updated_at)
                VALUES (?, ?, ?, NULL, 'rejected', 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id, source) DO UPDATE SET
                    confirmed_id = NULL,
                    state = 'rejected',
                    locked = 1,
                    note = excluded.note,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (entity_type, entity_id, source, note, actor),
            )
            conn.execute(
                """
                INSERT INTO match_override_events
                    (entity_type, entity_id, source, action, candidate_id, note, actor)
                VALUES (?, ?, ?, 'reject', ?, ?, ?)
                """,
                (entity_type, entity_id, source, candidate_id, note, actor),
            )
            _upsert_manual_meta(
                conn,
                source=source,
                entity_type=entity_type,
                entity_id=entity_id,
                status="not_found",
                confidence=0,
                manual_tag="manual_reject",
            )
    except Exception as e:
        logger.error("library_audit_reject: DB write failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return {"status": "ok"}


@router.post("/library/audit/unlock")
def library_audit_unlock(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Admin/manual escape hatch:
    clears a manual lock so Stage 2 auto-match can operate on that source again.
    """
    data = data or {}
    entity_type = str(data.get("entity_type", "")).strip()
    entity_id = str(data.get("entity_id", "")).strip()
    source = str(data.get("source", "")).strip().lower()
    note = str(data.get("note", "")).strip()[:1000] or None
    actor = str(data.get("actor", "")).strip()[:200] or None

    if not entity_type or not entity_id or source not in _ALLOWED_SOURCES:
        return JSONResponse(
            {"status": "error", "message": "entity_type, entity_id, source required"},
            status_code=400,
        )

    try:
        with rythmx_store._connect() as conn:
            _ensure_match_override_tables(conn)
            conn.execute(
                """
                UPDATE match_overrides
                SET locked = 0,
                    note = ?,
                    updated_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = ? AND entity_id = ? AND source = ?
                """,
                (note, actor, entity_type, entity_id, source),
            )
            conn.execute(
                """
                INSERT INTO match_override_events
                    (entity_type, entity_id, source, action, candidate_id, note, actor)
                VALUES (?, ?, ?, 'unlock', NULL, ?, ?)
                """,
                (entity_type, entity_id, source, note, actor),
            )
    except Exception as e:
        logger.error("library_audit_unlock: DB write failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return {"status": "ok"}
