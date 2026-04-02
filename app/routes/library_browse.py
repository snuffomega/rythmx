"""
library_browse.py — Read-only browse routes for the Library page.

Provides artist/album/track listing and detail views backed by lib_* tables.
All SQL uses ? placeholders. No business logic — raw queries only.
Router registered at /api/v1 in main.py (no prefix in route strings).
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_api_key)])


# Artist routes were extracted to app/routes/library/artists.py.


@router.get("/library/releases")
def library_releases_global(
    is_owned: int = Query(default=None),
    kind: str = Query(default=None),
    q: str = Query(default=None),
    sort: str = Query(default="date"),   # "date" | "artist"
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
):
    """Global releases feed — queryable across all artists.

    Used by the Forge New Music tab to display a cross-artist missing releases
    feed. All data is already collected in lib_releases; no external API calls.
    """
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

    order_col = "COALESCE(lr.release_date_itunes, lr.release_date_deezer) DESC" if sort == "date" else "lr.artist_name ASC, COALESCE(lr.release_date_itunes, lr.release_date_deezer) DESC"

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
    count_sql = f"""
        SELECT COUNT(*) FROM lib_releases lr {where}
    """

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

    # Fetch sibling editions (same canonical group)
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

    # Fetch tracks on demand — prefer iTunes (better data), fallback to Deezer
    tracks: list[dict] = []
    if release.get("itunes_album_id"):
        tracks = get_album_tracks_itunes(release["itunes_album_id"])
    if not tracks and release.get("deezer_album_id"):
        tracks = get_album_tracks_deezer(release["deezer_album_id"])

    return {"status": "ok", "release": release, "tracks": tracks, "siblings": siblings}


# ---------------------------------------------------------------------------
# Release user preferences
# ---------------------------------------------------------------------------

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
        # Verify release exists
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

        # Sync dismiss flag to lib_releases
        if dismissed is not None:
            conn.execute(
                "UPDATE lib_releases SET user_dismissed = ? WHERE id = ?",
                (1 if dismissed else 0, release_id),
            )
            artist_id = conn.execute(
                "SELECT artist_id FROM lib_releases WHERE id = ?", (release_id,)
            ).fetchone()
            artist_id = artist_id[0] if artist_id else None

    # Refresh missing counts for the affected artist only
    if dismissed is not None:
        try:
            rythmx_store.refresh_missing_counts(artist_id=artist_id)
        except Exception as e:
            logger.warning("refresh_missing_counts after prefs update failed: %s", e)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------

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
                   COALESCE(al.thumb_url_deezer, al.thumb_url_plex) AS thumb_url,
                   al.lastfm_tags_json,
                   ar.name AS artist_name
            FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
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
                   COALESCE(al.thumb_url_deezer, al.thumb_url_plex) AS thumb_url,
                   al.lastfm_tags_json,
                   ar.name AS artist_name
            FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
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
                   sample_rate, bit_depth, channel_count, replay_gain_track
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


# ---------------------------------------------------------------------------
# Track rating
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tracks (flat list)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Library Audit — low-confidence / unverified items
# ---------------------------------------------------------------------------

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
