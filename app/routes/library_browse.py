"""
library_browse.py — Read-only browse routes for the Library page.

Provides artist/album/track listing and detail views backed by lib_* tables.
All SQL uses ? placeholders. No business logic — raw queries only.
Blueprint registered at /api/v1 in main.py (no prefix in route strings).
"""
import logging
from flask import Blueprint, jsonify, request
from app.db import rythmx_store

logger = logging.getLogger(__name__)

library_browse_bp = Blueprint("library_browse", __name__)


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

@library_browse_bp.route("/library/artists")
def library_artists():
    q = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", 50)), 200)
    platform = request.args.get("platform", "all")

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
    return jsonify({"status": "ok", "artists": artists, "total": total, "page": page})


@library_browse_bp.route("/library/artists/<artist_id>")
def library_artist_detail(artist_id):
    with rythmx_store._connect() as conn:
        artist_row = conn.execute(
            """
            SELECT a.id, a.name, a.match_confidence, a.source_platform,
                   a.lastfm_tags_json,
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
            return jsonify({"status": "error", "message": "Artist not found"}), 404

        albums = conn.execute(
            """
            SELECT id, artist_id, title, year, record_type,
                   match_confidence, needs_verification, source_platform
            FROM lib_albums
            WHERE artist_id = ? AND removed_at IS NULL
            ORDER BY year DESC
            """,
            (artist_id,),
        ).fetchall()

        top_tracks = conn.execute(
            """
            SELECT t.id, t.album_id, t.artist_id, t.title,
                   t.track_number, t.disc_number, t.duration,
                   t.rating, t.play_count,
                   al.title AS album_title
            FROM lib_tracks t
            JOIN lib_albums al ON al.id = t.album_id
            WHERE al.artist_id = ? AND t.removed_at IS NULL AND al.removed_at IS NULL
            ORDER BY t.play_count DESC
            LIMIT 10
            """,
            (artist_id,),
        ).fetchall()

    return jsonify({
        "status": "ok",
        "artist": dict(artist_row),
        "albums": [dict(r) for r in albums],
        "top_tracks": [dict(r) for r in top_tracks],
    })


# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------

@library_browse_bp.route("/library/albums")
def library_albums():
    q = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", 50)), 200)
    platform = request.args.get("platform", "all")
    record_type = request.args.get("record_type", "all")

    where = ["al.removed_at IS NULL"]
    params: list = []
    if q:
        where.append("(lower(al.title) LIKE lower(?) OR lower(ar.name) LIKE lower(?))")
        params.extend([f"%{q}%", f"%{q}%"])
    if platform != "all":
        where.append("al.source_platform = ?")
        params.append(platform)
    if record_type != "all":
        where.append("al.record_type = ?")
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
            SELECT al.id, al.artist_id, al.title, al.year, al.record_type,
                   al.match_confidence, al.needs_verification, al.source_platform,
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
    return jsonify({"status": "ok", "albums": albums, "total": total, "page": page})


@library_browse_bp.route("/library/albums/<album_id>")
def library_album_detail(album_id):
    with rythmx_store._connect() as conn:
        album_row = conn.execute(
            """
            SELECT al.id, al.artist_id, al.title, al.year, al.record_type,
                   al.match_confidence, al.needs_verification, al.source_platform,
                   ar.name AS artist_name
            FROM lib_albums al
            JOIN lib_artists ar ON ar.id = al.artist_id
            WHERE al.id = ? AND al.removed_at IS NULL
            """,
            (album_id,),
        ).fetchone()

        if not album_row:
            return jsonify({"status": "error", "message": "Album not found"}), 404

        tracks = conn.execute(
            """
            SELECT id, album_id, artist_id, title,
                   track_number, disc_number, duration,
                   rating, play_count
            FROM lib_tracks
            WHERE album_id = ? AND removed_at IS NULL
            ORDER BY disc_number, track_number
            """,
            (album_id,),
        ).fetchall()

    return jsonify({
        "status": "ok",
        "album": dict(album_row),
        "tracks": [dict(r) for r in tracks],
    })


# ---------------------------------------------------------------------------
# Track rating
# ---------------------------------------------------------------------------

@library_browse_bp.route("/library/tracks/<track_id>/rating", methods=["PATCH"])
def library_rate_track(track_id):
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    if rating is None or not isinstance(rating, int) or not (0 <= rating <= 10):
        return jsonify({"status": "error", "message": "rating must be integer 0-10"}), 400

    with rythmx_store._connect() as conn:
        result = conn.execute(
            "UPDATE lib_tracks SET rating = ? WHERE id = ?",
            (rating, track_id),
        )
        if result.rowcount == 0:
            return jsonify({"status": "error", "message": "Track not found"}), 404

    # TODO Phase 14: write-back rating to platform (Plex, Navidrome, Jellyfin)
    return jsonify({"status": "ok", "track_id": track_id, "rating": rating})


# ---------------------------------------------------------------------------
# Tracks (flat list)
# ---------------------------------------------------------------------------

@library_browse_bp.route("/library/tracks")
def library_tracks():
    q = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", 100)), 500)

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
                   t.rating, t.play_count,
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
    return jsonify({"status": "ok", "tracks": tracks, "total": total, "page": page})
