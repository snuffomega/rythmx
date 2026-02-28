import logging
from flask import Blueprint, jsonify, request
from app.db import cc_store

logger = logging.getLogger(__name__)

acquisition_bp = Blueprint("acquisition", __name__)


@acquisition_bp.route("/api/acquisition/queue", methods=["GET"])
def acquisition_queue_get():
    status = request.args.get("status")
    playlist = request.args.get("playlist")
    rows = cc_store.get_queue(status=status, playlist_name=playlist)
    items = [
        {
            "id": r.get("id"),
            "artist": r.get("artist_name", ""),
            "album": r.get("album_title", ""),
            "kind": r.get("kind", "album"),
            "status": r.get("status", "pending"),
            "requested_by": r.get("requested_by"),
            "requested_at": r.get("created_at"),
            "release_date": r.get("release_date"),
        }
        for r in rows
    ]
    return jsonify({"status": "ok", "items": items})


@acquisition_bp.route("/api/acquisition/queue", methods=["POST"])
def acquisition_queue_add():
    data = request.get_json() or {}
    artist = data.get("artist_name", "").strip()
    album = data.get("album_title", "").strip()
    if not artist or not album:
        return jsonify({"status": "error", "message": "artist_name and album_title required"}), 400
    queue_id = cc_store.add_to_queue(
        artist_name=artist, album_title=album,
        release_date=data.get("release_date"),
        kind=data.get("kind"),
        source=data.get("source"),
        requested_by="manual",
    )
    return jsonify({"status": "ok", "queue_id": queue_id})


@acquisition_bp.route("/api/acquisition/stats", methods=["GET"])
def acquisition_stats():
    stats = cc_store.get_queue_stats()
    return jsonify({"status": "ok", **stats})


@acquisition_bp.route("/api/acquisition/check-now", methods=["POST"])
def acquisition_check_now():
    """Trigger the acquisition worker immediately (re-check submitted items)."""
    try:
        from app import acquisition
        acquisition.check_queue()
        stats = cc_store.get_queue_stats()
        return jsonify({"status": "ok", "message": "Acquisition worker run complete", **stats})
    except Exception as e:
        logger.warning("acquisition check-now failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500
