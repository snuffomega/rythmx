"""
releases.py - Release-focused library routes.

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


@router.get("/library/releases")
def library_releases_global(
    is_owned: int = Query(default=None),
    kind: str = Query(default=None),
    q: str = Query(default=None),
    sort: str = Query(default="date"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
):
    """Global releases feed - queryable across all artists."""
    offset = (page - 1) * per_page

    conditions = []
    params: list = []

    if is_owned is not None:
        conditions.append("lr.is_owned = ?")
        params.append(is_owned)

    if kind:
        kinds = [k.strip() for k in kind.split(",") if k.strip()]
        placeholders = ",".join("?" * len(kinds))
        conditions.append(
            f"COALESCE(lr.kind_deezer, lr.kind_itunes) IN ({placeholders})"
        )
        params.extend(kinds)

    if q:
        conditions.append("(lr.title LIKE ? OR lr.artist_name LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_col = (
        "COALESCE(lr.release_date_itunes, lr.release_date_deezer) DESC"
        if sort == "date"
        else "lr.artist_name ASC, COALESCE(lr.release_date_itunes, lr.release_date_deezer) DESC"
    )

    sql = f"""
        SELECT lr.id, lr.artist_id, lr.artist_name, lr.title,
               COALESCE(lr.release_date_itunes, lr.release_date_deezer) AS release_date,
               lr.is_owned, lr.user_dismissed,
               COALESCE(lr.kind_deezer, lr.kind_itunes,
                   CASE
                       WHEN lr.track_count IS NOT NULL AND lr.track_count <= 3 THEN 'single'
                       WHEN lr.track_count IS NOT NULL AND lr.track_count <= 6 THEN 'ep'
                       ELSE 'album'
                   END) AS kind,
               lr.version_type, lr.track_count,
               COALESCE(lr.thumb_url_deezer, lr.thumb_url_itunes) AS thumb_url,
               lr.catalog_source, lr.deezer_album_id, lr.itunes_album_id,
               lr.explicit, lr.label, lr.genre_itunes, lr.canonical_release_id
        FROM lib_releases lr
        {where}
        ORDER BY {order_col}
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM lib_releases lr {where}"

    with rythmx_store._connect() as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql, params + [per_page, offset]).fetchall()

    return {
        "status": "ok",
        "releases": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/library/releases/{release_id}")
def library_release_detail(release_id: str):
    """Return release metadata + on-demand track listing from iTunes/Deezer."""
    from app.clients.music_client import get_album_tracks_itunes, get_album_tracks_deezer

    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT id, artist_id, artist_name, title,
                   COALESCE(release_date_itunes, release_date_deezer) AS release_date,
                   COALESCE(
                       kind_deezer, kind_itunes,
                       CASE
                           WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                           WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                           ELSE 'album'
                       END
                   ) AS kind,
                   version_type, track_count,
                   COALESCE(thumb_url_deezer, thumb_url_itunes) AS thumb_url,
                   catalog_source,
                   deezer_album_id, itunes_album_id, explicit, label, genre_itunes,
                   canonical_release_id
            FROM lib_releases WHERE id = ?
            """,
            (release_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Release not found"}, status_code=404)

    release = dict(row)

    siblings: list[dict] = []
    if release.get("canonical_release_id"):
        with rythmx_store._connect() as conn:
            sib_rows = conn.execute(
                """
                SELECT id, title, version_type,
                       COALESCE(release_date_itunes, release_date_deezer) AS release_date,
                       COALESCE(thumb_url_deezer, thumb_url_itunes) AS thumb_url,
                       is_owned,
                       COALESCE(
                           kind_deezer, kind_itunes,
                           CASE
                               WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                               WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                               ELSE 'album'
                           END
                       ) AS kind
                FROM lib_releases
                WHERE canonical_release_id = ? AND id != ?
                ORDER BY
                    is_owned DESC,
                    CASE version_type WHEN 'original' THEN 0 ELSE 1 END,
                    COALESCE(release_date_itunes, release_date_deezer) ASC
                """,
                (release["canonical_release_id"], release_id),
            ).fetchall()
            siblings = [dict(s) for s in sib_rows]

    tracks: list[dict] = []
    if release.get("itunes_album_id"):
        tracks = get_album_tracks_itunes(release["itunes_album_id"])
    if not tracks and release.get("deezer_album_id"):
        tracks = get_album_tracks_deezer(release["deezer_album_id"])

    return {"status": "ok", "release": release, "tracks": tracks, "siblings": siblings}


@router.get("/library/releases/{release_id}/prefs")
def library_release_prefs(release_id: str):
    """Return user preferences for a release, or null if none set."""
    with rythmx_store._connect() as conn:
        row = conn.execute(
            "SELECT release_id, dismissed, priority, notes, updated_at, source "
            "FROM user_release_prefs WHERE release_id = ?",
            (release_id,),
        ).fetchone()
    return {"status": "ok", "prefs": dict(row) if row else None}


@router.put("/library/releases/{release_id}/prefs")
def library_update_release_prefs(
    release_id: str,
    data: Optional[dict[str, Any]] = Body(default=None),
):
    """Upsert user preferences for a release (dismiss, priority, notes)."""
    data = data or {}
    dismissed = data.get("dismissed")
    priority = data.get("priority")
    notes = data.get("notes")

    with rythmx_store._connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM lib_releases WHERE id = ?", (release_id,)
        ).fetchone()
        if not exists:
            return JSONResponse(
                {"status": "error", "message": "Release not found"}, status_code=404
            )

        conn.execute(
            """
            INSERT INTO user_release_prefs (release_id, dismissed, priority, notes, source, updated_at)
            VALUES (?, COALESCE(?, 0), COALESCE(?, 0), ?, 'manual', CURRENT_TIMESTAMP)
            ON CONFLICT(release_id) DO UPDATE SET
                dismissed = COALESCE(?, user_release_prefs.dismissed),
                priority = COALESCE(?, user_release_prefs.priority),
                notes = COALESCE(?, user_release_prefs.notes),
                source = 'manual',
                updated_at = CURRENT_TIMESTAMP
            """,
            (release_id, dismissed, priority, notes, dismissed, priority, notes),
        )

        if dismissed is not None:
            conn.execute(
                "UPDATE lib_releases SET user_dismissed = ? WHERE id = ?",
                (1 if dismissed else 0, release_id),
            )
            artist_id = conn.execute(
                "SELECT artist_id FROM lib_releases WHERE id = ?", (release_id,)
            ).fetchone()
            artist_id = artist_id[0] if artist_id else None

    if dismissed is not None:
        try:
            rythmx_store.refresh_missing_counts(artist_id=artist_id)
        except Exception as e:
            logger.warning("refresh_missing_counts after prefs update failed: %s", e)

    return {"status": "ok"}

