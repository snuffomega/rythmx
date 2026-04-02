"""
tracks.py - Track-focused library routes.

Extracted from library_browse.py to reduce route-module sprawl while keeping
all API paths stable.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.patch("/library/tracks/{track_id}/rating")
def library_rate_track(
    track_id: str,
    data: Optional[dict[str, Any]] = Body(default=None),
):
    data = data or {}
    rating = data.get("rating")
    if rating is None or not isinstance(rating, int) or not (0 <= rating <= 10):
        return JSONResponse(
            {"status": "error", "message": "rating must be integer 0-10"}, status_code=400
        )

    with rythmx_store._connect() as conn:
        result = conn.execute(
            "UPDATE lib_tracks SET rating = ? WHERE id = ?",
            (rating, track_id),
        )
        if result.rowcount == 0:
            return JSONResponse(
                {"status": "error", "message": "Track not found"}, status_code=404
            )

    # TODO Phase 14: write-back rating to platform (Plex, Navidrome, Jellyfin)
    return {"status": "ok", "track_id": track_id, "rating": rating}


@router.get("/library/tracks")
def library_tracks(
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, le=500),
    artist_id: Optional[str] = Query(default=None),
):
    q = q.strip()
    where = ["t.removed_at IS NULL"]
    params: list = []
    if q:
        where.append(
            "(lower(t.title) LIKE lower(?) OR lower(ar.name) LIKE lower(?) OR lower(al.title) LIKE lower(?))"
        )
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if artist_id:
        where.append("al.artist_id = ?")
        params.append(artist_id)

    where_clause = " AND ".join(where)
    offset = (page - 1) * per_page

    with rythmx_store._connect() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) FROM lib_tracks t
            JOIN lib_albums al ON al.id = t.album_id
            JOIN lib_artists ar ON ar.id = al.artist_id
            WHERE {where_clause}
            """,
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT t.id, t.album_id, t.artist_id, t.title,
                   t.track_number, t.disc_number, t.duration,
                   t.rating, t.play_count, t.tempo_deezer AS tempo,
                   t.codec, t.bitrate, t.bit_depth, t.sample_rate,
                   al.title AS album_title,
                   ar.name  AS artist_name
            FROM lib_tracks t
            JOIN lib_albums  al ON al.id = t.album_id
            JOIN lib_artists ar ON ar.id = al.artist_id
            WHERE {where_clause}
            ORDER BY ar.name COLLATE NOCASE, al.year DESC, t.disc_number, t.track_number
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    tracks = [dict(r) for r in rows]
    return {"status": "ok", "tracks": tracks, "total": total, "page": page}

