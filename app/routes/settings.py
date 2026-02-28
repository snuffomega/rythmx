import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from app.db import cc_store
from app import config, last_fm_client, plex_push, soulsync_api

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/api/settings", methods=["GET"])
def settings_get():
    from app.db import get_library_reader
    lr = get_library_reader()
    accessible = lr.is_db_accessible()
    return jsonify({
        "status": "ok",
        "lastfm_username": config.LASTFM_USERNAME,
        "lastfm_configured": bool(config.LASTFM_API_KEY and config.LASTFM_USERNAME),
        "plex_url": config.PLEX_URL,
        "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
        "soulsync_url": config.SOULSYNC_URL,
        "soulsync_db": config.SOULSYNC_DB,
        "soulsync_db_accessible": accessible,
        "library_backend": cc_store.get_setting("library_backend") or config.LIBRARY_BACKEND,
        "library_accessible": accessible,
        "library_track_count": lr.get_track_count() if accessible else 0,
        "library_last_synced": cc_store.get_setting("library_last_synced"),
    })


@settings_bp.route("/api/settings/test-lastfm", methods=["POST"])
def settings_test_lastfm():
    result = last_fm_client.test_connection()
    ok = result.get("status") == "ok"
    msg = result.get("username") if ok else result.get("message", "Connection failed")
    return jsonify({"connected": ok, "message": msg})


@settings_bp.route("/api/settings/test-plex", methods=["POST"])
def settings_test_plex():
    result = plex_push.test_connection()
    ok = result.get("status") == "ok"
    return jsonify({"connected": ok, "message": result.get("message") if not ok else None})


@settings_bp.route("/api/settings/test-soulsync", methods=["POST"])
def settings_test_soulsync():
    active_backend = cc_store.get_setting("library_backend") or "soulsync"

    if active_backend == "navidrome":
        return jsonify({"connected": False, "message": "Navidrome not yet implemented"})
    if active_backend == "jellyfin":
        return jsonify({"connected": False, "message": "Jellyfin not yet implemented"})
    if active_backend == "plex":
        import os as _os, sqlite3 as _sq
        db_path = config.LIBRARY_DB
        if not _os.path.exists(db_path):
            return jsonify({"connected": False,
                            "message": "Library DB not synced yet — click Sync Library"})
        try:
            with _sq.connect(db_path) as _c:
                count = _c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            return jsonify({"connected": True, "message": f"{count:,} tracks indexed"})
        except Exception as e:
            return jsonify({"connected": False, "message": str(e)})
    # soulsync (default)
    from app.db import soulsync_reader as _ss_reader
    db_available = _ss_reader.is_db_accessible()
    api_status = soulsync_api.test_connection()
    api_ok = api_status.get("status") == "ok"
    ok = db_available or api_ok
    if db_available:
        msg = "DB accessible"
    elif api_ok:
        msg = "API reachable (DB not mounted)"
    else:
        msg = api_status.get("message") or "Not accessible"
    return jsonify({"connected": ok, "message": msg})


@settings_bp.route("/api/settings/test-spotify", methods=["POST"])
def settings_test_spotify():
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return jsonify({"connected": False,
                        "message": "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET not set"})
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
        sp.search(q="test", type="artist", limit=1)
        return jsonify({"connected": True})
    except Exception as e:
        return jsonify({"connected": False, "message": str(e)})


@settings_bp.route("/api/settings/test-fanart", methods=["POST"])
def settings_test_fanart():
    if not config.FANART_API_KEY:
        return jsonify({"connected": False, "message": "FANART_API_KEY not set — add to .env"})
    try:
        import requests as _req
        resp = _req.get(
            "https://webservice.fanart.tv/v3/music/a74b1b7f-71a5-4011-9441-d0b5e4122711",
            params={"api_key": config.FANART_API_KEY},
            timeout=10,
        )
        if resp.status_code == 401:
            return jsonify({"connected": False, "message": "Invalid API key"})
        if resp.status_code == 200:
            return jsonify({"connected": True, "message": "Connected"})
        return jsonify({"connected": False, "message": f"HTTP {resp.status_code}"})
    except Exception as e:
        return jsonify({"connected": False, "message": str(e)})


@settings_bp.route("/api/library/status", methods=["GET"])
def library_status():
    from app.db import get_library_reader
    lr = get_library_reader()
    accessible = lr.is_db_accessible()
    backend = cc_store.get_setting("library_backend") or config.LIBRARY_BACKEND
    return jsonify({
        "status": "ok",
        "backend": backend,
        "accessible": accessible,
        "track_count": lr.get_track_count() if accessible else 0,
        "last_synced": cc_store.get_setting("library_last_synced"),
    })


@settings_bp.route("/api/library/sync", methods=["POST"])
def library_sync():
    from app.db import get_library_reader
    try:
        lr = get_library_reader()
        result = lr.sync_library()
        cc_store.set_setting(
            "library_last_synced",
            datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )
        return jsonify({"status": "ok", **result})
    except NotImplementedError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.warning("library sync failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@settings_bp.route("/api/settings/library-backend", methods=["POST"])
def settings_set_library_backend():
    data = request.get_json() or {}
    backend = data.get("backend", "").lower()
    if backend not in {"soulsync", "plex", "navidrome", "jellyfin"}:
        return jsonify({"status": "error", "message": f"Invalid backend: {backend}"}), 400
    cc_store.set_setting("library_backend", backend)
    return jsonify({"status": "ok", "backend": backend})


@settings_bp.route("/api/settings/clear-history", methods=["POST"])
def settings_clear_history():
    cc_store.clear_history()
    return jsonify({"status": "ok"})


@settings_bp.route("/api/settings/reset-db", methods=["POST"])
def settings_reset_db():
    cc_store.reset_db()
    return jsonify({"status": "ok"})


@settings_bp.route("/api/settings/clear-image-cache", methods=["POST"])
def settings_clear_image_cache():
    cc_store.clear_image_cache()
    return jsonify({"status": "ok", "message": "Image cache cleared"})
