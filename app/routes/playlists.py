import logging
from flask import Blueprint, jsonify, request
from app.db import cc_store
from app import last_fm_client, plex_push

logger = logging.getLogger(__name__)

playlists_bp = Blueprint("playlists", __name__)


def _build_taste_playlist_tracks(playlist_name: str) -> list[dict]:
    """Build + save a taste-based playlist. Returns the saved tracks."""
    from app import engine
    from app.db import get_library_reader
    sr = get_library_reader()

    meta = cc_store.get_playlist_meta(playlist_name) or {}
    max_tracks = int(meta.get("max_tracks") or 50)
    max_per_artist = int(meta.get("max_per_artist") or 2)

    top_artists = last_fm_client.get_top_artists(period="6month", limit=200)
    loved = last_fm_client.get_loved_artist_names()

    logger.info(
        "Taste build '%s': %d top artists, %d loved, max_tracks=%d",
        playlist_name, len(top_artists), len(loved), max_tracks,
    )

    artist_tracks = {}
    resolved_count = 0
    for artist_name in top_artists:
        cached = cc_store.get_cached_artist(artist_name) or {}
        ss_id = cached.get("soulsync_artist_id") or sr.get_soulsync_artist_id(artist_name)
        if ss_id:
            tracks = sr.get_all_tracks_for_artist(ss_id)
            if tracks:
                artist_tracks[artist_name] = tracks
                resolved_count += 1
                logger.info("  ✓ %s → %d library tracks", artist_name, len(tracks))
            else:
                logger.info("  ~ %s — found in SoulSync but 0 tracks returned", artist_name)
        else:
            logger.info("  ✗ %s — not found in SoulSync library", artist_name)

    logger.info(
        "Taste build: %d/%d artists resolved to library tracks",
        resolved_count, len(top_artists),
    )

    scored = engine.build_taste_playlist(
        top_artists, loved, artist_tracks,
        limit=max_tracks, max_per_artist=max_per_artist,
    )

    to_save = [
        {
            "plex_rating_key": t["plex_rating_key"],
            "spotify_track_id": t.get("spotify_track_id"),
            "track_name": t["track_name"],
            "artist_name": t["artist_name"],
            "album_name": t["album_name"],
            "album_cover_url": t.get("album_cover_url", ""),
            "score": t["score"],
        }
        for t in scored
    ]
    cc_store.save_playlist(to_save, playlist_name=playlist_name)
    cc_store.mark_playlist_synced(playlist_name)
    return to_save


def _import_external_playlist(source: str, source_url: str) -> dict:
    """Import tracks from an external source. Returns importer result dict."""
    from app import playlist_importer
    if source == "lastfm":
        return playlist_importer.import_from_lastfm(source_url)
    if source == "deezer":
        return playlist_importer.import_from_deezer(source_url)
    return playlist_importer.import_from_spotify(source_url)


def _shape_imported_tracks(result: dict) -> list[dict]:
    return [
        {
            "plex_rating_key": t["plex_rating_key"],
            "spotify_track_id": t.get("spotify_track_id", ""),
            "track_name": t["track_name"],
            "artist_name": t["artist_name"],
            "album_name": t["album_name"],
            "album_cover_url": "",
            "score": None,
        }
        for t in result["tracks"]
    ]


@playlists_bp.route("/api/playlists", methods=["GET"])
def playlists_list():
    playlists = cc_store.list_playlists()
    return jsonify({"status": "ok", "playlists": playlists})


@playlists_bp.route("/api/playlists", methods=["POST"])
def playlists_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "name is required"}), 400
    if cc_store.get_playlist_meta(name):
        return jsonify({"status": "error", "message": f"Playlist '{name}' already exists"}), 409

    source = data.get("source", "manual")
    source_url = data.get("source_url") or None
    auto_sync = bool(data.get("auto_sync", False))
    mode = data.get("mode", "library_only")
    max_tracks = int(data.get("max_tracks") or 50)

    if source not in ("taste", "spotify", "lastfm", "deezer", "manual"):
        return jsonify({"status": "error",
                        "message": "source must be taste, spotify, lastfm, deezer, or manual"}), 400

    cc_store.create_playlist_meta(name, source=source, source_url=source_url,
                                  auto_sync=auto_sync, mode=mode, max_tracks=max_tracks)

    track_count = 0
    owned_count = 0

    if source == "taste":
        try:
            tracks = _build_taste_playlist_tracks(name)
            track_count = len(tracks)
            owned_count = sum(1 for t in tracks if t.get("plex_rating_key"))
        except Exception as e:
            logger.error("Taste playlist build failed for '%s': %s", name, e)
            return jsonify({"status": "error", "message": f"Build failed: {e}"}), 500

    elif source in ("spotify", "lastfm", "deezer"):
        if not source_url:
            return jsonify({"status": "error",
                            "message": f"source_url required for {source} import"}), 400
        result = _import_external_playlist(source, source_url)
        if result["status"] != "ok":
            return jsonify(result), 400
        cc_store.save_playlist(_shape_imported_tracks(result), playlist_name=name)
        cc_store.mark_playlist_synced(name)
        track_count = result["track_count"]
        owned_count = result["owned_count"]

    return jsonify({"status": "ok", "name": name,
                    "track_count": track_count, "owned_count": owned_count})


@playlists_bp.route("/api/playlists/<path:name>", methods=["DELETE"])
def playlists_delete(name):
    cc_store.delete_playlist(name)
    return jsonify({"status": "ok"})


@playlists_bp.route("/api/playlists/<path:name>/tracks", methods=["GET"])
def playlists_tracks(name):
    rows = cc_store.get_playlist(playlist_name=name)
    tracks = [
        {
            "row_id":             r["id"],
            "track_id":           r.get("track_id"),
            "name":               r.get("track_name", ""),
            "artist":             r.get("artist_name", ""),
            "album":              r.get("album_name", ""),
            "image":              r.get("album_cover_url"),
            "is_owned":           bool(r.get("is_owned", 0)),
            "score":              r.get("score"),
            "acquisition_status": (
                cc_store.get_queue_status(
                    r.get("artist_name", ""), r.get("album_name", "")
                )
                if not r.get("is_owned", 0) else None
            ),
        }
        for r in (rows or [])
    ]
    return jsonify({"status": "ok", "tracks": tracks})


@playlists_bp.route("/api/playlists/<path:name>", methods=["PATCH"])
def playlists_update(name):
    """Update playlist metadata (auto_sync, mode, max_tracks, source_url, new_name)."""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("new_name") or "").strip()
    if new_name and new_name != name:
        cc_store.rename_playlist(name, new_name)
        return jsonify({"status": "ok", "name": new_name})
    cc_store.update_playlist_meta(
        name,
        auto_sync=bool(data["auto_sync"]) if data.get("auto_sync") is not None else None,
        mode=data.get("mode"),
        max_tracks=int(data["max_tracks"]) if data.get("max_tracks") is not None else None,
        source_url=data.get("source_url"),
    )
    return jsonify({"status": "ok"})


@playlists_bp.route("/api/playlists/<path:name>/tracks/<int:row_id>", methods=["DELETE"])
def playlists_remove_track(name, row_id):
    """Remove a single track row from a playlist by cc_playlist.id."""
    cc_store.remove_playlist_row(row_id)
    return jsonify({"status": "ok"})


@playlists_bp.route("/api/playlists/<path:name>/build", methods=["POST"])
def playlists_build(name):
    if not cc_store.get_playlist_meta(name):
        cc_store.create_playlist_meta(name, source="taste")
    try:
        tracks = _build_taste_playlist_tracks(name)
        return jsonify({"status": "ok", "track_count": len(tracks),
                        "owned_count": sum(1 for t in tracks if t.get("plex_rating_key"))})
    except Exception as e:
        logger.error("Playlist build failed for '%s': %s", name, e)
        return jsonify({"status": "error", "message": str(e)}), 500


@playlists_bp.route("/api/playlists/<path:name>/rebuild", methods=["POST"])
def playlists_rebuild(name):
    """Alias for /build — rebuild a taste-based playlist."""
    if not cc_store.get_playlist_meta(name):
        cc_store.create_playlist_meta(name, source="taste")
    try:
        tracks = _build_taste_playlist_tracks(name)
        return jsonify({"status": "ok", "track_count": len(tracks),
                        "owned_count": sum(1 for t in tracks if t.get("plex_rating_key"))})
    except Exception as e:
        logger.error("Playlist rebuild failed for '%s': %s", name, e)
        return jsonify({"status": "error", "message": str(e)}), 500


def _sync_external(name: str, req_data: dict) -> tuple:
    """Shared logic for /import and /sync endpoints."""
    meta = cc_store.get_playlist_meta(name) or {}
    source = meta.get("source", "spotify")
    source_url = meta.get("source_url") or req_data.get("source_url")
    if not source_url:
        return None, jsonify({"status": "error", "message": "No source_url for this playlist"}), 400
    result = _import_external_playlist(source, source_url)
    if result["status"] != "ok":
        return None, jsonify(result), 400
    cc_store.save_playlist(_shape_imported_tracks(result), playlist_name=name)
    cc_store.mark_playlist_synced(name)
    return result, None, None


@playlists_bp.route("/api/playlists/<path:name>/import", methods=["POST"])
def playlists_import(name):
    result, err_resp, err_code = _sync_external(name, request.get_json(silent=True) or {})
    if err_resp:
        return err_resp, err_code
    return jsonify({"status": "ok", "track_count": result["track_count"],
                    "owned_count": result["owned_count"]})


@playlists_bp.route("/api/playlists/<path:name>/sync", methods=["POST"])
def playlists_sync(name):
    """Alias for /import — re-import playlist from external source."""
    result, err_resp, err_code = _sync_external(name, request.get_json(silent=True) or {})
    if err_resp:
        return err_resp, err_code
    return jsonify({"status": "ok", "track_count": result["track_count"],
                    "owned_count": result["owned_count"]})


@playlists_bp.route("/api/playlists/<path:name>/publish", methods=["POST"])
def playlists_publish(name):
    tracks = cc_store.get_playlist(playlist_name=name)
    rating_keys = [t["track_id"] for t in tracks if t.get("track_id") and t.get("is_owned", 1)]
    if not rating_keys:
        return jsonify({"status": "error", "message": "No owned tracks in playlist to push"}), 400
    playlist_id = plex_push.create_or_update_playlist(name, rating_keys)
    if playlist_id:
        cc_store.update_playlist_plex_id(name, playlist_id)
        return jsonify({"status": "ok", "plex_playlist_id": playlist_id})
    return jsonify({"status": "error", "message": "Plex push failed — check logs"}), 500


@playlists_bp.route("/api/playlists/<path:name>/export", methods=["POST"])
def playlists_export(name):
    tracks = cc_store.get_playlist(playlist_name=name)
    if not tracks:
        return jsonify({"status": "error", "message": "Playlist is empty"}), 400
    lines = ["#EXTM3U"]
    for t in tracks:
        lines.append(f"#EXTINF:-1,{t.get('artist_name', '')} - {t.get('track_name', '')}")
        if t.get("spotify_track_id"):
            lines.append(f"# spotify:{t['spotify_track_id']}")
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
    return jsonify({"status": "ok", "content": "\n".join(lines),
                    "filename": f"{safe_name}.m3u"})


@playlists_bp.route("/api/playlists/<path:name>/settings", methods=["POST"])
def playlists_settings(name):
    """Update auto_sync / mode for a playlist."""
    data = request.get_json(silent=True) or {}
    cc_store.update_playlist_meta(
        name,
        auto_sync=bool(data["auto_sync"]) if data.get("auto_sync") is not None else None,
        mode=data.get("mode"),
    )
    return jsonify({"status": "ok"})
