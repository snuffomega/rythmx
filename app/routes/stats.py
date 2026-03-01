import logging
from flask import Blueprint, jsonify, request
from app.db import cc_store
from app.clients import last_fm_client

logger = logging.getLogger(__name__)

stats_bp = Blueprint("stats", __name__)


@stats_bp.route("/api/stats/top-artists")
def stats_top_artists():
    period = request.args.get("period", "6month")
    limit = min(int(request.args.get("limit", 50)), 200)
    ranked = last_fm_client.get_top_artists_ranked(period=period, limit=limit)
    return jsonify({"status": "ok", "artists": ranked, "period": period})


@stats_bp.route("/api/stats/top-tracks")
def stats_top_tracks():
    period = request.args.get("period", "6month")
    limit = min(int(request.args.get("limit", 50)), 200)
    tracks = last_fm_client.get_top_tracks(period=period, limit=limit)
    return jsonify({"status": "ok", "tracks": tracks, "period": period})


@stats_bp.route("/api/stats/top-albums")
def stats_top_albums():
    period = request.args.get("period", "6month")
    limit = min(int(request.args.get("limit", 50)), 200)
    albums = last_fm_client.get_top_albums(period=period, limit=limit)
    return jsonify({"status": "ok", "albums": albums, "period": period})


@stats_bp.route("/api/stats/summary")
def stats_summary():
    summary = cc_store.get_history_summary()
    return jsonify({"status": "ok", "summary": summary})


@stats_bp.route("/api/stats/loved-artists")
def stats_loved_artists():
    loved = last_fm_client.get_loved_artist_names()
    artists = [{"name": name} for name in sorted(loved)]
    return jsonify({"status": "ok", "artists": artists})
