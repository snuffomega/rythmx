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
from app.services.enrichment._helpers import strip_title_suffixes

logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_api_key)])


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

@router.get("/library/artists")
def library_artists(
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
    platform: str = "all",
):
    q = q.strip()
    where = ["a.removed_at IS NULL"]
    params: list = []
    if q:
        where.append("lower(a.name) LIKE lower(?)")
        params.append(f"%{q}%")
    if platform != "all":
        where.append("a.source_platform = ?")
        params.append(platform)

    where_clause = " AND ".join(where)
    offset = (page - 1) * per_page

    with rythmx_store._connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM lib_artists a WHERE {where_clause}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT a.id, a.name, a.match_confidence, a.source_platform,
                   a.lastfm_tags_json,
                   a.genres_json_spotify AS genres_json,
                   a.popularity_spotify AS popularity,
                   a.listener_count_lastfm AS listener_count,
                   a.play_count_lastfm AS global_play_count,
                   a.missing_count,
                   COALESCE(a.image_url_fanart, a.image_url_deezer) AS image_url,
                   COUNT(al.id) AS album_count
            FROM lib_artists a
            LEFT JOIN lib_albums al
                   ON al.artist_id = a.id AND al.removed_at IS NULL
            WHERE {where_clause}
            GROUP BY a.id
            ORDER BY a.name COLLATE NOCASE
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    artists = [dict(r) for r in rows]
    return {"status": "ok", "artists": artists, "total": total, "page": page}


@router.get("/library/artists/{artist_id}")
def library_artist_detail(artist_id: str):
    with rythmx_store._connect() as conn:
        artist_row = conn.execute(
            """
            SELECT a.id, a.name, a.match_confidence, a.source_platform,
                   a.lastfm_tags_json,
                   a.genres_json_spotify AS genres_json,
                   a.popularity_spotify AS popularity,
                   a.listener_count_lastfm AS listener_count,
                   a.play_count_lastfm AS global_play_count,
                   COALESCE(a.image_url_fanart, a.image_url_deezer) AS image_url,
                   COUNT(al.id) AS album_count
            FROM lib_artists a
            LEFT JOIN lib_albums al
                   ON al.artist_id = a.id AND al.removed_at IS NULL
            WHERE a.id = ? AND a.removed_at IS NULL
            GROUP BY a.id
            """,
            (artist_id,),
        ).fetchone()

        if not artist_row:
            return JSONResponse(
                {"status": "error", "message": "Artist not found"}, status_code=404
            )

        albums = conn.execute(
            """
            SELECT la.id, la.artist_id, la.title, la.year,
                   COALESCE(la.record_type_deezer,
                       CASE
                           WHEN tc.cnt IS NOT NULL AND tc.cnt <= 3 THEN 'single'
                           WHEN tc.cnt IS NOT NULL AND tc.cnt <= 6 THEN 'ep'
                           ELSE 'album'
                       END
                   ) AS record_type,
                   la.match_confidence, la.needs_verification, la.source_platform,
                   la.release_date_itunes AS release_date, la.genre_itunes AS genre,
                   COALESCE(la.thumb_url_deezer, la.thumb_url_plex) AS thumb_url,
                   la.lastfm_tags_json
            FROM lib_albums la
            LEFT JOIN (
                SELECT album_id, COUNT(*) AS cnt
                FROM lib_tracks WHERE removed_at IS NULL
                GROUP BY album_id
            ) tc ON tc.album_id = la.id
            WHERE la.artist_id = ? AND la.removed_at IS NULL
            ORDER BY la.year DESC
            """,
            (artist_id,),
        ).fetchall()

        top_tracks = conn.execute(
            """
            SELECT t.id, t.album_id, t.artist_id, t.title,
                   t.track_number, t.disc_number, t.duration,
                   t.rating, t.play_count, t.tempo_deezer AS tempo,
                   al.title AS album_title
            FROM lib_tracks t
            JOIN lib_albums al ON al.id = t.album_id
            WHERE al.artist_id = ? AND t.removed_at IS NULL AND al.removed_at IS NULL
            ORDER BY t.play_count DESC
            LIMIT 10
            """,
            (artist_id,),
        ).fetchall()

        # --- Missing albums: pre-computed from lib_releases ---
        try:
            missing_rows = conn.execute(
                """
                WITH base AS (
                    SELECT *,
                           COALESCE(
                               kind_deezer, kind_itunes,
                               CASE
                                   WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                   WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                   ELSE 'album'
                               END
                           ) AS resolved_kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY artist_name_lower, normalized_title,
                                            COALESCE(
                                                kind_deezer, kind_itunes,
                                                CASE
                                                    WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                                    WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                                    ELSE 'album'
                                                END
                                            )
                               ORDER BY
                                   CASE catalog_source WHEN 'deezer' THEN 1 WHEN 'itunes' THEN 2 ELSE 3 END,
                                   thumb_url IS NOT NULL DESC,
                                   release_date IS NOT NULL DESC
                           ) AS rn
                    FROM lib_releases
                    WHERE artist_id = ? AND is_owned = 0 AND user_dismissed = 0
                )
                SELECT title AS album_title, resolved_kind AS kind, resolved_kind AS record_type,
                       version_type, release_date, catalog_source AS source,
                       deezer_album_id, itunes_album_id, thumb_url, track_count, id
                FROM base
                WHERE rn = 1
                  AND NOT (
                      resolved_kind = 'single'
                      AND EXISTS (
                          SELECT 1 FROM lib_releases lr2
                          WHERE lr2.artist_name_lower = base.artist_name_lower
                            AND lr2.normalized_title = base.normalized_title
                            AND COALESCE(lr2.kind_deezer, lr2.kind_itunes, 'album') IN ('album', 'ep')
                            AND lr2.id != base.id
                      )
                  )
                ORDER BY release_date DESC
                """,
                (artist_id,),
            ).fetchall()
        except Exception:
            missing_rows = []

        dismissed_count = conn.execute(
            "SELECT COUNT(*) FROM lib_releases WHERE artist_id = ? AND user_dismissed = 1",
            (artist_id,),
        ).fetchone()[0]

        # --- Grouped missing releases (canonical edition groups) ---
        missing_groups = []
        try:
            group_rows = conn.execute(
                """
                SELECT id, title AS album_title, version_type, release_date,
                       catalog_source AS source, deezer_album_id, itunes_album_id,
                       thumb_url, track_count, is_owned, canonical_release_id,
                       COALESCE(
                           kind_deezer, kind_itunes,
                           CASE
                               WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                               WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                               ELSE 'album'
                           END
                       ) AS kind
                FROM lib_releases
                WHERE artist_id = ?
                  AND user_dismissed = 0
                  AND canonical_release_id IS NOT NULL
                ORDER BY canonical_release_id,
                         is_owned DESC,
                         CASE version_type WHEN 'original' THEN 0 ELSE 1 END,
                         release_date ASC
                """,
                (artist_id,),
            ).fetchall()

            from collections import OrderedDict
            groups: OrderedDict[str, dict] = OrderedDict()
            for row in group_rows:
                cid = row["canonical_release_id"]
                if cid not in groups:
                    groups[cid] = {"primary": None, "editions": []}
                edition = dict(row)
                edition["display_title"] = strip_title_suffixes(edition["album_title"])
                groups[cid]["editions"].append(edition)
                if groups[cid]["primary"] is None:
                    groups[cid]["primary"] = edition

            for cid, group in groups.items():
                all_owned = all(e["is_owned"] for e in group["editions"])
                if all_owned:
                    continue
                missing_groups.append({
                    "canonical_release_id": cid,
                    "primary": group["primary"],
                    "edition_count": len(group["editions"]),
                    "owned_count": sum(1 for e in group["editions"] if e["is_owned"]),
                    "editions": group["editions"],
                    "kind": group["primary"]["kind"],
                })
        except Exception:
            missing_groups = []

    return {
        "status": "ok",
        "artist": dict(artist_row),
        "albums": [dict(r) for r in albums],
        "top_tracks": [dict(r) for r in top_tracks],
        "missing_albums": [{**dict(r), "display_title": strip_title_suffixes(r["album_title"])} for r in missing_rows],
        "missing_groups": missing_groups,
        "dismissed_count": dismissed_count,
    }


@router.get("/library/releases/{release_id}")
def library_release_detail(release_id: str):
    """Return release metadata + on-demand track listing from iTunes/Deezer."""
    from app.clients.music_client import get_album_tracks_itunes, get_album_tracks_deezer

    with rythmx_store._connect() as conn:
        row = conn.execute(
            """
            SELECT id, artist_id, artist_name, title, release_date,
                   COALESCE(
                       kind_deezer, kind_itunes,
                       CASE
                           WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                           WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                           ELSE 'album'
                       END
                   ) AS kind,
                   version_type, track_count, thumb_url, catalog_source,
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
                SELECT id, title, version_type, release_date, thumb_url, is_owned,
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
                    release_date ASC
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

    # Refresh missing counts if dismiss state changed
    if dismissed is not None:
        try:
            rythmx_store.refresh_missing_counts()
        except Exception as e:
            logger.warning("refresh_missing_counts after prefs update failed: %s", e)

    return {"status": "ok"}


@router.get("/library/artists/{artist_id}/match-debug")
def library_artist_match_debug(artist_id: str):
    """
    Diagnostic endpoint: re-runs match_album_title() against stored catalogs.
    Shows per-album match scores for both iTunes and Deezer so you can see
    exactly WHY an album matched or didn't.  No API calls — reads from
    lib_artist_catalog (populated during enrichment).
    """
    from app.services.enrichment._helpers import match_album_title

    with rythmx_store._connect() as conn:
        artist = conn.execute(
            "SELECT id, name, itunes_artist_id, deezer_artist_id, match_confidence "
            "FROM lib_artists WHERE id = ? AND removed_at IS NULL",
            (artist_id,),
        ).fetchone()
        if not artist:
            return JSONResponse({"status": "error", "message": "Artist not found"}, 404)

        albums = conn.execute(
            "SELECT id, title, local_title, itunes_album_id, deezer_id "
            "FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL "
            "ORDER BY title COLLATE NOCASE",
            (artist_id,),
        ).fetchall()

        # Load catalogs grouped by source
        catalog_rows = conn.execute(
            "SELECT source, album_id, album_title, record_type, track_count "
            "FROM lib_artist_catalog WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()

        itunes_catalog = [r for r in catalog_rows if r["source"] == "itunes"]
        deezer_catalog = [r for r in catalog_rows if r["source"] == "deezer"]

        # Get track counts per album from lib_tracks
        album_ids = [a["id"] for a in albums]
        track_counts: dict[str, int] = {}
        if album_ids:
            ph = ",".join("?" * len(album_ids))
            for row in conn.execute(
                f"SELECT album_id, COUNT(*) AS cnt FROM lib_tracks WHERE album_id IN ({ph}) GROUP BY album_id",
                album_ids,
            ).fetchall():
                track_counts[row["album_id"]] = row["cnt"]

    def _best_match(album_title, catalog):
        if not catalog:
            return {"status": "no_catalog"}
        best_title, best_score, best_entry = None, 0.0, None
        for entry in catalog:
            s = match_album_title(album_title, entry["album_title"])
            if s > best_score:
                best_score = s
                best_title = entry["album_title"]
                best_entry = entry
        if best_entry is None:
            return {"status": "no_catalog"}
        result = {
            "status": "matched" if best_score >= 0.82 else "below_threshold",
            "best_title": best_title,
            "best_id": best_entry["album_id"],
            "score": round(best_score, 3),
        }
        if best_entry["track_count"]:
            result["api_tracks"] = best_entry["track_count"]
        if best_entry["record_type"]:
            result["record_type"] = best_entry["record_type"]
        return result

    items = []
    for album in albums:
        title = album["local_title"] or album["title"]
        entry = {
            "album_id": album["id"],
            "library_title": title,
            "library_tracks": track_counts.get(album["id"], 0),
            "itunes": _best_match(title, itunes_catalog),
            "deezer": _best_match(title, deezer_catalog),
        }
        # Override status if already matched (stored ID exists)
        if album["itunes_album_id"] and entry["itunes"].get("best_id"):
            entry["itunes"]["status"] = "matched"
        if album["deezer_id"] and entry["deezer"].get("best_id"):
            entry["deezer"]["status"] = "matched"
        items.append(entry)

    return {
        "status": "ok",
        "artist": artist["name"],
        "artist_id": artist["id"],
        "itunes_artist_id": artist["itunes_artist_id"],
        "deezer_artist_id": artist["deezer_artist_id"],
        "artist_confidence": artist["match_confidence"],
        "catalog_size": {"itunes": len(itunes_catalog), "deezer": len(deezer_catalog)},
        "albums": items,
    }


@router.get("/library/artists/{artist_id}/release-groups")
def library_release_groups(artist_id: str):
    """Diagnostic: show canonical release groups for an artist.

    Returns groups where the same normalized_title has multiple editions,
    along with per-group member details. Useful for validating canonical
    linking quality before enabling auto-collapse in the UI.
    """
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT canonical_release_id,
                   GROUP_CONCAT(id, ',') AS member_ids,
                   GROUP_CONCAT(title, ' | ') AS titles,
                   GROUP_CONCAT(version_type, ',') AS version_types,
                   COUNT(*) AS edition_count,
                   MAX(is_owned) AS any_owned
            FROM lib_releases
            WHERE artist_id = ? AND canonical_release_id IS NOT NULL
            GROUP BY canonical_release_id
            HAVING COUNT(*) > 1
            ORDER BY edition_count DESC
            """,
            (artist_id,),
        ).fetchall()
    return {"status": "ok", "groups": [dict(g) for g in rows]}


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
                   al.release_date_itunes AS release_date, al.genre_itunes AS genre,
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
                   al.release_date_itunes AS release_date, al.genre_itunes AS genre,
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
                   rating, play_count, tempo_deezer AS tempo
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
):
    q = q.strip()
    where = ["t.removed_at IS NULL"]
    params: list = []
    if q:
        where.append(
            "(lower(t.title) LIKE lower(?) OR lower(ar.name) LIKE lower(?) OR lower(al.title) LIKE lower(?))"
        )
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

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
