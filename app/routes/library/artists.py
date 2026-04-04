"""
artists.py - Artist-focused library routes.

Extracted from library_browse.py to reduce route-module sprawl while keeping
all API paths stable.
"""
import logging
import json
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key
from app.services.enrichment._helpers import strip_title_suffixes

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

_ARTIST_TOP_TRACKS_LIMIT = 10
_DEEZER_TOP_TRACKS_FETCH_LIMIT = 50
_DEEZER_TOP_TRACKS_TTL_DAYS = 30


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _get_local_top_tracks(
    conn,
    artist_id: str,
    *,
    limit: int = _ARTIST_TOP_TRACKS_LIMIT,
    exclude_track_ids: Optional[set[str]] = None,
    popularity_source: str = "local",
) -> list[dict[str, Any]]:
    params: list[Any] = [artist_id]
    exclude_sql = ""
    if exclude_track_ids:
        placeholders = ",".join("?" for _ in exclude_track_ids)
        exclude_sql = f" AND t.id NOT IN ({placeholders})"
        params.extend(sorted(exclude_track_ids))
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT t.id, t.album_id, t.artist_id, t.title,
               t.track_number, t.disc_number, t.duration,
               t.rating, t.play_count, t.tempo_deezer AS tempo,
               t.sample_rate, t.bit_depth, t.channel_count, t.replay_gain_track,
               t.bitrate, t.codec, t.container, t.embedded_lyrics, t.tag_genre,
               al.title AS album_title
        FROM lib_tracks t
        JOIN lib_albums al ON al.id = t.album_id
        WHERE al.artist_id = ? AND t.removed_at IS NULL AND al.removed_at IS NULL
        {exclude_sql}
        ORDER BY
            COALESCE(t.play_count, 0) DESC,
            COALESCE(t.rating, 0) DESC,
            lower(t.title) ASC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    results = [dict(r) for r in rows]
    for row in results:
        row["public_rank_position"] = None
        row["public_popularity"] = None
        row["popularity_source"] = popularity_source
    return results


def _load_cached_deezer_top_tracks(conn, deezer_artist_id: str) -> list[dict[str, Any]] | None:
    try:
        row = conn.execute(
            """
            SELECT tracks_json
            FROM lib_artist_top_tracks_cache
            WHERE artist_deezer_id = ?
              AND (expires_at IS NULL OR expires_at >= ?)
            """,
            (deezer_artist_id, _now_iso()),
        ).fetchone()
    except Exception as exc:
        logger.debug("artist_detail: top-track cache lookup unavailable: %s", exc)
        return None

    if not row:
        return None
    try:
        parsed = json.loads(str(row["tracks_json"] or "[]"))
        return parsed if isinstance(parsed, list) else None
    except Exception:
        return None


def _save_cached_deezer_top_tracks(conn, deezer_artist_id: str, tracks: list[dict[str, Any]]) -> None:
    now = datetime.utcnow()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    expires_at = (now + timedelta(days=_DEEZER_TOP_TRACKS_TTL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn.execute(
            """
            INSERT INTO lib_artist_top_tracks_cache
                (artist_deezer_id, tracks_json, fetched_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(artist_deezer_id) DO UPDATE SET
                tracks_json = excluded.tracks_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (deezer_artist_id, json.dumps(tracks), now_iso, expires_at),
        )
    except Exception as exc:
        logger.debug("artist_detail: failed to write top-track cache: %s", exc)


def _fetch_deezer_top_tracks(conn, deezer_artist_id: str) -> list[dict[str, Any]]:
    from app.clients.music_client import get_deezer_artist_top_tracks

    tracks = get_deezer_artist_top_tracks(deezer_artist_id, limit=_DEEZER_TOP_TRACKS_FETCH_LIMIT)
    if tracks:
        _save_cached_deezer_top_tracks(conn, deezer_artist_id, tracks)
    return tracks or []


def _get_public_top_tracks(conn, artist_id: str, deezer_artist_id: str) -> list[dict[str, Any]]:
    if not deezer_artist_id:
        return []

    external_tracks = _load_cached_deezer_top_tracks(conn, deezer_artist_id)
    if external_tracks is None:
        external_tracks = _fetch_deezer_top_tracks(conn, deezer_artist_id)
    if not external_tracks:
        return []

    deezer_track_ids = [
        str(t.get("id") or t.get("deezer_track_id") or "").strip()
        for t in external_tracks
        if str(t.get("id") or t.get("deezer_track_id") or "").strip()
    ]
    if not deezer_track_ids:
        return []

    placeholders = ",".join("?" for _ in deezer_track_ids)
    rows = conn.execute(
        f"""
        SELECT t.id, t.album_id, t.artist_id, t.title,
               t.track_number, t.disc_number, t.duration,
               t.rating, t.play_count, t.tempo_deezer AS tempo,
               t.sample_rate, t.bit_depth, t.channel_count, t.replay_gain_track,
               t.bitrate, t.codec, t.container, t.embedded_lyrics, t.tag_genre,
               t.deezer_id AS deezer_track_id,
               al.title AS album_title
        FROM lib_tracks t
        JOIN lib_albums al ON al.id = t.album_id
        WHERE t.artist_id = ?
          AND t.removed_at IS NULL
          AND al.removed_at IS NULL
          AND t.deezer_id IN ({placeholders})
        """,
        (artist_id, *deezer_track_ids),
    ).fetchall()

    best_by_deezer_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        deezer_track_id = str(payload.get("deezer_track_id") or "").strip()
        if not deezer_track_id:
            continue
        prev = best_by_deezer_id.get(deezer_track_id)
        if prev is None:
            best_by_deezer_id[deezer_track_id] = payload
            continue
        prev_score = (int(prev.get("play_count") or 0), int(prev.get("rating") or 0))
        curr_score = (int(payload.get("play_count") or 0), int(payload.get("rating") or 0))
        if curr_score > prev_score:
            best_by_deezer_id[deezer_track_id] = payload

    ranked: list[dict[str, Any]] = []
    used_track_ids: set[str] = set()
    for i, ext in enumerate(external_tracks, start=1):
        deezer_track_id = str(ext.get("id") or ext.get("deezer_track_id") or "").strip()
        if not deezer_track_id:
            continue
        local_match = best_by_deezer_id.get(deezer_track_id)
        if not local_match:
            continue

        local_match["public_rank_position"] = int(ext.get("rank_position") or i)
        local_match["public_popularity"] = int(ext.get("deezer_rank") or 0)
        local_match["popularity_source"] = "deezer"
        ranked.append(local_match)
        used_track_ids.add(str(local_match.get("id") or ""))
        if len(ranked) >= _ARTIST_TOP_TRACKS_LIMIT:
            break

    if len(ranked) < _ARTIST_TOP_TRACKS_LIMIT:
        fillers = _get_local_top_tracks(
            conn,
            artist_id,
            limit=_ARTIST_TOP_TRACKS_LIMIT - len(ranked),
            exclude_track_ids=used_track_ids,
            popularity_source="local_fallback",
        )
        ranked.extend(fillers)

    return ranked[:_ARTIST_TOP_TRACKS_LIMIT]


@router.get("/library/artists/filter-options")
def library_artist_filter_options():
    """Returns available decade and region filter values for the artist list."""
    with rythmx_store._connect() as conn:
        decade_rows = conn.execute(
            """
            SELECT DISTINCT (formed_year_musicbrainz / 10) * 10 AS decade
            FROM lib_artists
            WHERE formed_year_musicbrainz IS NOT NULL AND removed_at IS NULL
            ORDER BY decade DESC
            """
        ).fetchall()
        region_rows = conn.execute(
            """
            SELECT DISTINCT area_musicbrainz AS region
            FROM lib_artists
            WHERE area_musicbrainz IS NOT NULL AND removed_at IS NULL
            ORDER BY area_musicbrainz COLLATE NOCASE
            """
        ).fetchall()
    return {
        "status": "ok",
        "decades": [r["decade"] for r in decade_rows],
        "regions": [r["region"] for r in region_rows],
    }


@router.get("/library/artists")
def library_artists(
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
    platform: str = "all",
    decade: Optional[int] = Query(default=None),
    region: Optional[str] = Query(default=None),
    letter: Optional[str] = Query(default=None),
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
    if decade is not None:
        where.append("(a.formed_year_musicbrainz / 10) * 10 = ?")
        params.append(decade)
    if region:
        where.append("lower(a.area_musicbrainz) LIKE lower(?)")
        params.append(region)
    if letter:
        letter = letter.strip().upper()
        sort_expr = """CASE
            WHEN lower(a.name) LIKE 'the %%' THEN substr(a.name, 5)
            WHEN lower(a.name) LIKE 'a %%'   THEN substr(a.name, 3)
            WHEN lower(a.name) LIKE 'an %%'  THEN substr(a.name, 4)
            ELSE a.name
        END"""
        if letter == "#":
            where.append(f"upper(substr(({sort_expr}), 1, 1)) NOT BETWEEN 'A' AND 'Z'")
        else:
            where.append(f"upper(substr(({sort_expr}), 1, 1)) = ?")
            params.append(letter)

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
                   COALESCE(ia.image_url, a.image_url_fanart, a.image_url_deezer) AS image_url,
                   ia.content_hash AS image_hash,
                   COUNT(al.id) AS album_count,
                   CASE
                       WHEN lower(a.name) LIKE 'the %' THEN substr(a.name, 5)
                       WHEN lower(a.name) LIKE 'a %'   THEN substr(a.name, 3)
                       WHEN lower(a.name) LIKE 'an %'  THEN substr(a.name, 4)
                       ELSE a.name
                   END AS sort_name
            FROM lib_artists a
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'artist' AND ia.entity_key = a.id
            LEFT JOIN lib_albums al
                   ON al.artist_id = a.id AND al.removed_at IS NULL
            WHERE {where_clause}
            GROUP BY a.id
            ORDER BY
                CASE
                    WHEN lower(a.name) LIKE 'the %' THEN substr(a.name, 5)
                    WHEN lower(a.name) LIKE 'a %'   THEN substr(a.name, 3)
                    WHEN lower(a.name) LIKE 'an %'  THEN substr(a.name, 4)
                    ELSE a.name
                END COLLATE NOCASE
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
                   a.deezer_artist_id,
                   a.listener_count_lastfm AS listener_count,
                   a.play_count_lastfm AS global_play_count,
                   COALESCE(ia.image_url, a.image_url_fanart, a.image_url_deezer) AS image_url,
                   ia.content_hash AS image_hash,
                   COUNT(al.id) AS album_count,
                   a.bio_lastfm,
                   a.fans_deezer,
                   a.similar_artists_json,
                   a.area_musicbrainz,
                   a.begin_area_musicbrainz,
                   a.formed_year_musicbrainz
            FROM lib_artists a
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'artist' AND ia.entity_key = a.id
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
                   COALESCE(la.original_release_date_musicbrainz, la.release_date_itunes,
                            la.year || '-01-01') AS release_date,
                   la.genre_itunes AS genre,
                   COALESCE(ia.image_url, la.thumb_url_deezer, la.thumb_url_plex) AS thumb_url,
                   ia.content_hash AS thumb_hash,
                   la.lastfm_tags_json
            FROM lib_albums la
            LEFT JOIN image_cache ia
                   ON ia.entity_type = 'album' AND ia.entity_key = la.id
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

        deezer_artist_id = str(artist_row["deezer_artist_id"] or "").strip()
        top_tracks = _get_public_top_tracks(conn, artist_id, deezer_artist_id)
        if not top_tracks:
            top_tracks = _get_local_top_tracks(conn, artist_id, limit=_ARTIST_TOP_TRACKS_LIMIT)

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
                                   COALESCE(thumb_url_deezer, thumb_url_itunes) IS NOT NULL DESC,
                                   COALESCE(release_date_itunes, release_date_deezer) IS NOT NULL DESC
                           ) AS rn
                    FROM lib_releases
                    WHERE artist_id = ? AND is_owned = 0 AND user_dismissed = 0
                )
                SELECT title AS album_title, resolved_kind AS kind, resolved_kind AS record_type,
                       version_type,
                       COALESCE(release_date_itunes, release_date_deezer) AS release_date,
                       catalog_source AS source,
                       deezer_album_id, itunes_album_id,
                       COALESCE(thumb_url_deezer, thumb_url_itunes) AS thumb_url,
                       track_count, id
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

        missing_groups = []
        try:
            group_rows = conn.execute(
                """
                SELECT id, title AS album_title, version_type,
                       COALESCE(release_date_itunes, release_date_deezer) AS release_date,
                       catalog_source AS source, deezer_album_id, itunes_album_id,
                       COALESCE(thumb_url_deezer, thumb_url_itunes) AS thumb_url,
                       track_count, is_owned, canonical_release_id,
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
                missing_groups.append(
                    {
                        "canonical_release_id": cid,
                        "primary": group["primary"],
                        "edition_count": len(group["editions"]),
                        "owned_count": sum(1 for e in group["editions"] if e["is_owned"]),
                        "editions": group["editions"],
                        "kind": group["primary"]["kind"],
                    }
                )
        except Exception:
            missing_groups = []

    return {
        "status": "ok",
        "artist": {k: v for k, v in dict(artist_row).items() if k != "deezer_artist_id"},
        "albums": [dict(r) for r in albums],
        "top_tracks": top_tracks,
        "missing_albums": [
            {**dict(r), "display_title": strip_title_suffixes(r["album_title"])}
            for r in missing_rows
        ],
        "missing_groups": missing_groups,
        "dismissed_count": dismissed_count,
    }


@router.get("/library/artists/{artist_id}/similar")
def library_artist_similar(artist_id: str):
    """Resolve similar_artists_json entries against the local library."""
    import json as _json

    with rythmx_store._connect() as conn:
        row = conn.execute(
            "SELECT similar_artists_json FROM lib_artists WHERE id = ? AND removed_at IS NULL",
            (artist_id,),
        ).fetchone()

    if not row or not row["similar_artists_json"]:
        return {"status": "ok", "similar": []}

    try:
        raw: list[dict] = _json.loads(row["similar_artists_json"])
    except (ValueError, TypeError):
        return {"status": "ok", "similar": []}

    with rythmx_store._connect() as conn:
        lib_rows = conn.execute(
            "SELECT id, name FROM lib_artists WHERE removed_at IS NULL"
        ).fetchall()

    lib_by_name: dict[str, str] = {r["name"].lower(): r["id"] for r in lib_rows}

    result = []
    for entry in raw[:20]:
        name = entry.get("name", "").strip()
        if not name:
            continue
        lib_id = lib_by_name.get(name.lower())
        if lib_id:
            result.append({"name": name, "in_library": True, "library_id": lib_id})
        else:
            result.append({"name": name, "in_library": False})

    return {"status": "ok", "similar": result}


@router.post("/library/artists/{artist_id}/cover")
def library_artist_set_cover(
    artist_id: str,
    data: Optional[dict[str, Any]] = Body(default=None),
):
    """Store a custom cover URL for an artist in the image cache."""
    data = data or {}
    cover_url = str(data.get("cover_url", "")).strip()
    if not cover_url or not cover_url.startswith("http"):
        return JSONResponse(
            {"status": "error", "message": "cover_url must be a valid http(s) URL"},
            status_code=400,
        )

    with rythmx_store._connect() as conn:
        row = conn.execute(
            "SELECT id FROM lib_artists WHERE id = ? AND removed_at IS NULL",
            (artist_id,),
        ).fetchone()
    if not row:
        return JSONResponse(
            {"status": "error", "message": "Artist not found"}, status_code=404
        )

    rythmx_store.set_image_cache_entry(
        "artist",
        artist_id,
        cover_url,
        local_path=None,
        content_hash=None,
        artwork_source=None,
    )
    return {"status": "ok"}


@router.get("/library/artists/{artist_id}/match-debug")
def library_artist_match_debug(artist_id: str):
    """
    Diagnostic endpoint: re-runs match_album_title() against stored catalogs.
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

        catalog_rows = conn.execute(
            "SELECT source, album_id, album_title, record_type, track_count "
            "FROM lib_artist_catalog WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()

        itunes_catalog = [r for r in catalog_rows if r["source"] == "itunes"]
        deezer_catalog = [r for r in catalog_rows if r["source"] == "deezer"]

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
    """Diagnostic: show canonical release groups for an artist."""
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
