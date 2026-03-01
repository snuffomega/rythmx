import logging
from flask import Blueprint, jsonify, request
from app.db import cc_store
from app.clients import last_fm_client, plex_push, soulsync_api

logger = logging.getLogger(__name__)

dash_bp = Blueprint("dash", __name__)


@dash_bp.route("/api/discovery/candidates")
def discovery_candidates():
    from app.db import get_library_reader
    from app.services import engine
    sr = get_library_reader()

    candidates = sr.get_discovery_pool(limit=200)
    similar_map = sr.get_similar_artists_map()
    top_artists = last_fm_client.get_top_artists()
    loved = last_fm_client.get_loved_artist_names()

    candidates = engine.apply_owned_check(candidates, sr)
    scored = engine.score_candidates(candidates, similar_map, top_artists, loved)
    return jsonify({"status": "ok", "candidates": scored})


@dash_bp.route("/api/discovery/playlist", methods=["GET"])
def discovery_playlist():
    tracks = cc_store.get_playlist()
    return jsonify({"status": "ok", "playlist": tracks})


@dash_bp.route("/api/discovery/playlist", methods=["POST"])
def discovery_playlist_add():
    data = request.get_json(silent=True) or {}
    if not data.get("track_id") and not data.get("spotify_track_id"):
        return jsonify({"status": "error", "message": "track_id or spotify_track_id required"}), 400
    cc_store.add_to_playlist(data)
    return jsonify({"status": "ok"})


@dash_bp.route("/api/discovery/playlist/<path:track_id>", methods=["DELETE"])
def discovery_playlist_remove(track_id):
    cc_store.remove_from_playlist(track_id)
    return jsonify({"status": "ok"})


@dash_bp.route("/api/discovery/download", methods=["POST"])
def discovery_download():
    data = request.get_json(silent=True) or {}
    track = {
        "track_name": data.get("track_name"),
        "artist_name": data.get("artist_name"),
        "album_name": data.get("album_name"),
        "spotify_track_id": data.get("spotify_track_id"),
    }
    if not track["track_name"] or not track["artist_name"]:
        return jsonify({"status": "error", "message": "track_name and artist_name required"}), 400
    result = soulsync_api.queue_download(track)
    return jsonify(result)


@dash_bp.route("/api/discovery/publish", methods=["POST"])
def discovery_publish():
    tracks = cc_store.get_playlist()
    rating_keys = [t["track_id"] for t in tracks if t.get("track_id")]
    if not rating_keys:
        return jsonify({"status": "error", "message": "No owned tracks in playlist to push"}), 400
    cc_store.create_playlist_meta("For You", source="cc", mode="library_only")
    playlist_id = plex_push.create_or_update_playlist("For You", rating_keys)
    if playlist_id:
        cc_store.update_playlist_plex_id("For You", playlist_id)
        return jsonify({"status": "ok", "plex_playlist_id": playlist_id})
    return jsonify({"status": "error", "message": "Plex push failed — check logs"}), 500


@dash_bp.route("/api/discovery/export", methods=["POST"])
def discovery_export():
    tracks = cc_store.get_playlist()
    if not tracks:
        return jsonify({"status": "error", "message": "Playlist is empty"}), 400
    lines = ["#EXTM3U"]
    for t in tracks:
        lines.append(f"#EXTINF:-1,{t.get('artist_name', '')} - {t.get('track_name', '')}")
        if t.get("spotify_track_id"):
            lines.append(f"# spotify:{t['spotify_track_id']}")
    return jsonify({"status": "ok", "content": "\n".join(lines), "filename": "for-you.m3u"})
