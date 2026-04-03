"""
albums.py - Album-focused library routes.

Extracted from library_browse.py to reduce route-module sprawl while keeping
all API paths stable.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/library/albums")
def library_albums(
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
    platform: str = "all",
    record_type: str = "all",
):
    q = q.strip()
    where = ["al.removed_at IS NULL"]
    params: list = []
    if q:
        where.append("(lower(al.title) LIKE lower(?) OR lower(ar.name) LIKE lower(?))")
        params.extend([f"%{q}%", f"%{q}%"])
    if platform != "all":
        where.append("al.source_platform = ?")
        params.append(platform)
    if record_type != "all":
        where.append("al.record_type_deezer = ?")
        params.append(record_type)

    where_clause = " AND ".join(where)
    offset = (page - 1) * per_page

    with rythmx_store._connect() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
            WHERE {where_clause}
            """,
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT al.id, al.artist_id, al.title, al.year,
                   al.record_type_deezer AS record_type,
                   al.match_confidence, al.needs_verification, al.source_platform,
                   COALESCE(al.original_release_date_musicbrainz, al.release_date_itunes,
                            al.year || '-01-01') AS release_date,
                   al.genre_itunes AS genre,
                   COALESCE(ia.image_url, al.thumb_url_deezer, al.thumb_url_plex) AS thumb_url,
                   ia.content_hash AS thumb_hash,
                   al.lastfm_tags_json,
                   ar.name AS artist_name
            FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'album' AND ia.entity_key = al.id
            WHERE {where_clause}
            ORDER BY ar.name COLLATE NOCASE, al.year DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    albums = [dict(r) for r in rows]
    return {"status": "ok", "albums": albums, "total": total, "page": page}


@router.get("/library/albums/{album_id}")
def library_album_detail(album_id: str):
    with rythmx_store._connect() as conn:
        album_row = conn.execute(
            """
            SELECT al.id, al.artist_id, al.title, al.year,
                   al.record_type_deezer AS record_type,
                   al.match_confidence, al.needs_verification, al.source_platform,
                   COALESCE(al.original_release_date_musicbrainz, al.release_date_itunes,
                            al.year || '-01-01') AS release_date,
                   al.genre_itunes AS genre,
                   COALESCE(ia.image_url, al.thumb_url_deezer, al.thumb_url_plex) AS thumb_url,
                   ia.content_hash AS thumb_hash,
                   al.lastfm_tags_json,
                   ar.name AS artist_name
            FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'album' AND ia.entity_key = al.id
            WHERE al.id = ? AND al.removed_at IS NULL
            """,
            (album_id,),
        ).fetchone()

        if not album_row:
            return JSONResponse(
                {"status": "error", "message": "Album not found"}, status_code=404
            )

        tracks = conn.execute(
            """
            SELECT id, album_id, artist_id, title,
                   track_number, disc_number, duration,
                   rating, play_count, tempo_deezer AS tempo,
                   sample_rate, bit_depth, channel_count, replay_gain_track,
                   bitrate, codec, container, embedded_lyrics, tag_genre
            FROM lib_tracks
            WHERE album_id = ? AND removed_at IS NULL
            ORDER BY disc_number, track_number
            """,
            (album_id,),
        ).fetchall()

    return {
        "status": "ok",
        "album": dict(album_row),
        "tracks": [dict(r) for r in tracks],
    }


@router.post("/library/albums/{album_id}/cover")
def library_album_set_cover(
    album_id: str,
    data: Optional[dict[str, Any]] = Body(default=None),
):
    """Store a custom cover URL for an album in the image cache."""
    data = data or {}
    cover_url = str(data.get("cover_url", "")).strip()
    if not cover_url or not cover_url.startswith("http"):
        return JSONResponse(
            {"status": "error", "message": "cover_url must be a valid http(s) URL"},
            status_code=400,
        )

    with rythmx_store._connect() as conn:
        row = conn.execute(
            "SELECT id FROM lib_albums WHERE id = ? AND removed_at IS NULL",
            (album_id,),
        ).fetchone()
    if not row:
        return JSONResponse(
            {"status": "error", "message": "Album not found"}, status_code=404
        )

    rythmx_store.set_image_cache_entry(
        "album",
        album_id,
        cover_url,
        local_path=None,
        content_hash=None,
        artwork_source=None,
    )
    return {"status": "ok"}
