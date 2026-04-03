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

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


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
                   la.id AS album_id, la.title AS album_title,
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

    items = []
    for r in rows:
        items.append({
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "album_id": r["album_id"],
            "album_title": r["album_title"],
            "match_confidence": r["match_confidence"],
            "needs_verification": bool(r["needs_verification"]),
            "itunes_album_id": r["itunes_album_id"],
            "deezer_id": r["deezer_id"],
            "enrichment": meta_map.get(r["album_id"], {}),
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
    Body: { entity_type, entity_id, source, confirmed_id }
    Sets needs_verification=0, match_confidence=100, writes the confirmed ID.
    """
    data = data or {}
    entity_type = str(data.get("entity_type", "")).strip()
    entity_id = str(data.get("entity_id", "")).strip()
    source = str(data.get("source", "")).strip()
    confirmed_id = str(data.get("confirmed_id", "")).strip()[:200]

    if not entity_type or not entity_id or not source or not confirmed_id:
        return JSONResponse(
            {"status": "error",
             "message": "entity_type, entity_id, source, confirmed_id required"},
            status_code=400,
        )

    id_col_map = {
        "itunes": "itunes_album_id",
        "deezer": "deezer_id",
        "spotify": "spotify_album_id",
    }
    id_col = id_col_map.get(source) if entity_type == "album" else None

    try:
        with rythmx_store._connect() as conn:
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
                INSERT OR REPLACE INTO enrichment_meta
                    (source, entity_type, entity_id, status, enriched_at, confidence)
                VALUES (?, ?, ?, 'found', CURRENT_TIMESTAMP, 100)
                """,
                (source, entity_type, entity_id),
            )
    except Exception as e:
        logger.error("library_audit_confirm: DB write failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return {"status": "ok"}


@router.post("/library/audit/reject")
def library_audit_reject(data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Reject an incorrect enrichment match.
    Body: { entity_type, entity_id, source }
    Clears the ID column, sets match_confidence=0, needs_verification=1.
    """
    data = data or {}
    entity_type = str(data.get("entity_type", "")).strip()
    entity_id = str(data.get("entity_id", "")).strip()
    source = str(data.get("source", "")).strip()

    if not entity_type or not entity_id or not source:
        return JSONResponse(
            {"status": "error", "message": "entity_type, entity_id, source required"},
            status_code=400,
        )

    id_col_map = {
        "itunes": "itunes_album_id",
        "deezer": "deezer_id",
        "spotify": "spotify_album_id",
    }
    id_col = id_col_map.get(source) if entity_type == "album" else None

    try:
        with rythmx_store._connect() as conn:
            if id_col and entity_type == "album":
                conn.execute(
                    f"""
                    UPDATE lib_albums
                    SET {id_col} = NULL,
                        match_confidence = 0,
                        needs_verification = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (entity_id,),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO enrichment_meta
                    (source, entity_type, entity_id, status, enriched_at, confidence)
                VALUES (?, ?, ?, 'not_found', CURRENT_TIMESTAMP, 0)
                """,
                (source, entity_type, entity_id),
            )
    except Exception as e:
        logger.error("library_audit_reject: DB write failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return {"status": "ok"}
