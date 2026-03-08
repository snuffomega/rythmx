import logging
import threading
from datetime import datetime
from flask import Blueprint, jsonify, request
from app.db import rythmx_store
from app import config
from app.clients import last_fm_client, plex_push, soulsync_api

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

# Track background thread state
_sync_thread: threading.Thread | None = None
_sync_lock = threading.Lock()
_enrich_thread: threading.Thread | None = None
_enrich_lock = threading.Lock()
_spotify_enrich_thread: threading.Thread | None = None
_spotify_enrich_lock = threading.Lock()
_lastfm_tags_thread: threading.Thread | None = None
_lastfm_tags_lock = threading.Lock()
_deezer_bpm_thread: threading.Thread | None = None
_deezer_bpm_lock = threading.Lock()


def _spawn_sync_thread() -> None:
    """Start a background library sync if one isn't already running."""
    global _sync_thread
    with _sync_lock:
        if _sync_thread is not None and _sync_thread.is_alive():
            return

        def _run():
            from app.services import library_service as _lib_svc
            try:
                result = _lib_svc.sync_library()
                rythmx_store.set_setting(
                    "library_last_synced",
                    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                )
                logger.info("Background library sync complete: %s", result)
            except Exception as e:
                logger.error("Background library sync failed: %s", e)

        _sync_thread = threading.Thread(target=_run, daemon=True, name="lib-sync")
        _sync_thread.start()


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
        "library_backend": rythmx_store.get_setting("library_backend") or config.LIBRARY_BACKEND,
        "library_accessible": accessible,
        "library_track_count": lr.get_track_count() if accessible else 0,
        "library_last_synced": rythmx_store.get_setting("library_last_synced"),
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
    active_backend = rythmx_store.get_setting("library_backend") or "soulsync"

    if active_backend == "navidrome":
        return jsonify({"connected": False, "message": "Navidrome not yet implemented"})
    if active_backend == "jellyfin":
        return jsonify({"connected": False, "message": "Jellyfin not yet implemented"})
    if active_backend == "plex":
        import os as _os, sqlite3 as _sq
        db_path = config.RYTHMX_DB
        if not _os.path.exists(db_path):
            return jsonify({"connected": False,
                            "message": "Library DB not synced yet — click Sync Library"})
        try:
            with _sq.connect(db_path) as _c:
                tbl = _c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='lib_tracks'"
                ).fetchone()
                if not tbl:
                    return jsonify({"connected": False,
                                    "message": "Library not synced yet — click Sync Library"})
                count = _c.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()[0]
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
    from app.services import library_service
    status = library_service.get_status()
    return jsonify({"status": "ok", **status})


@settings_bp.route("/api/library/sync", methods=["POST"])
def library_sync():
    from app.db import get_library_reader
    try:
        lr = get_library_reader()
        result = lr.sync_library()
        rythmx_store.set_setting(
            "library_last_synced",
            datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )
        return jsonify({"status": "ok", **result})
    except NotImplementedError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.warning("library sync failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@settings_bp.route("/api/library/enrich-status", methods=["GET"])
def library_enrich_status():
    from app.services import library_service
    global _enrich_thread
    status = library_service.get_status()
    running = _enrich_thread is not None and _enrich_thread.is_alive()
    return jsonify({"status": "ok", "enrich_running": running, **status})


@settings_bp.route("/api/library/enrich", methods=["POST"])
def library_enrich():
    global _enrich_thread
    with _enrich_lock:
        if _enrich_thread is not None and _enrich_thread.is_alive():
            return jsonify({"status": "ok", "message": "Enrich already running"}), 202

        data = request.get_json(silent=True) or {}
        batch_size = int(data.get("batch_size", 50))

        def _run():
            from app.services import library_service as _lib_svc
            try:
                result = _lib_svc.enrich_library(batch_size=batch_size)
                logger.info("Library enrich complete: %s", result)
            except Exception as e:
                logger.error("Library enrich failed: %s", e)

        _enrich_thread = threading.Thread(target=_run, daemon=True, name="lib-enrich")
        _enrich_thread.start()

    return jsonify({"status": "ok", "message": "Enrich started"}), 202


@settings_bp.route("/api/library/spotify-status", methods=["GET"])
def library_spotify_status():
    from app.services import library_service
    global _spotify_enrich_thread
    status = library_service.get_spotify_status()
    running = _spotify_enrich_thread is not None and _spotify_enrich_thread.is_alive()
    return jsonify({"status": "ok", "enrich_running": running, **status})


@settings_bp.route("/api/library/enrich-spotify", methods=["POST"])
def library_enrich_spotify():
    global _spotify_enrich_thread
    with _spotify_enrich_lock:
        if _spotify_enrich_thread is not None and _spotify_enrich_thread.is_alive():
            return jsonify({"status": "ok", "message": "Spotify enrich already running"}), 202

        data = request.get_json(silent=True) or {}
        batch_size = int(data.get("batch_size", 20))

        def _run():
            from app.services import library_service as _lib_svc
            from app.db import rythmx_store as _store
            try:
                result = _lib_svc.enrich_spotify(batch_size=batch_size)
                _store.set_setting("spotify_enrich_last_run", datetime.utcnow().isoformat())
                logger.info("Spotify enrich complete: %s", result)
            except Exception as e:
                logger.error("Spotify enrich failed: %s", e)

        _spotify_enrich_thread = threading.Thread(target=_run, daemon=True, name="spotify-enrich")
        _spotify_enrich_thread.start()

    return jsonify({"status": "ok", "message": "Spotify enrich started"}), 202


@settings_bp.route("/api/library/lastfm-tags-status", methods=["GET"])
def library_lastfm_tags_status():
    from app.services import library_service
    global _lastfm_tags_thread
    status = library_service.get_lastfm_tags_status()
    running = _lastfm_tags_thread is not None and _lastfm_tags_thread.is_alive()
    return jsonify({"status": "ok", "enrich_running": running, **status})


@settings_bp.route("/api/library/enrich-lastfm-tags", methods=["POST"])
def library_enrich_lastfm_tags():
    global _lastfm_tags_thread
    with _lastfm_tags_lock:
        if _lastfm_tags_thread is not None and _lastfm_tags_thread.is_alive():
            return jsonify({"status": "ok", "message": "Last.fm tag enrich already running"}), 202

        data = request.get_json(silent=True) or {}
        batch_size = int(data.get("batch_size", 50))

        def _run():
            from app.services import library_service as _lib_svc
            from app.db import rythmx_store as _store
            try:
                result = _lib_svc.enrich_lastfm_tags(batch_size=batch_size)
                _store.set_setting("lastfm_tags_last_run", datetime.utcnow().isoformat())
                logger.info("Last.fm tag enrich complete: %s", result)
            except Exception as e:
                logger.error("Last.fm tag enrich failed: %s", e)

        _lastfm_tags_thread = threading.Thread(target=_run, daemon=True, name="lastfm-tags-enrich")
        _lastfm_tags_thread.start()

    return jsonify({"status": "ok", "message": "Last.fm tag enrich started"}), 202


@settings_bp.route("/api/library/deezer-bpm-status", methods=["GET"])
def library_deezer_bpm_status():
    from app.services import library_service
    global _deezer_bpm_thread
    status = library_service.get_deezer_bpm_status()
    running = _deezer_bpm_thread is not None and _deezer_bpm_thread.is_alive()
    return jsonify({"status": "ok", "enrich_running": running, **status})


@settings_bp.route("/api/library/enrich-deezer-bpm", methods=["POST"])
def library_enrich_deezer_bpm():
    global _deezer_bpm_thread
    with _deezer_bpm_lock:
        if _deezer_bpm_thread is not None and _deezer_bpm_thread.is_alive():
            return jsonify({"status": "ok", "message": "Deezer BPM enrich already running"}), 202

        data = request.get_json(silent=True) or {}
        batch_size = int(data.get("batch_size", 30))

        def _run():
            from app.services import library_service as _lib_svc
            from app.db import rythmx_store as _store
            try:
                result = _lib_svc.enrich_deezer_bpm(batch_size=batch_size)
                _store.set_setting("deezer_bpm_last_run", datetime.utcnow().isoformat())
                logger.info("Deezer BPM enrich complete: %s", result)
            except Exception as e:
                logger.error("Deezer BPM enrich failed: %s", e)

        _deezer_bpm_thread = threading.Thread(target=_run, daemon=True, name="deezer-bpm-enrich")
        _deezer_bpm_thread.start()

    return jsonify({"status": "ok", "message": "Deezer BPM enrich started"}), 202


@settings_bp.route("/api/settings/library-backend", methods=["POST"])
def settings_set_library_backend():
    data = request.get_json(silent=True) or {}
    backend = data.get("backend", "").lower()
    if backend not in {"soulsync", "plex", "navidrome", "jellyfin"}:
        return jsonify({"status": "error", "message": f"Invalid backend: {backend}"}), 400
    rythmx_store.set_setting("library_backend", backend)

    # Auto-trigger an initial sync when soulsync backend is set and lib_* is empty.
    # This imports SoulSync's pre-enriched IDs (confidence=95) so enrichment passes
    # only need to fill the remaining gaps rather than starting from scratch.
    auto_sync_started = False
    if backend == "soulsync":
        try:
            from app.db import plex_reader as _pr
            with _pr._connect() as _conn:
                count = _conn.execute(
                    "SELECT COUNT(*) FROM lib_artists WHERE source_backend = 'soulsync'"
                ).fetchone()[0]
            if count == 0:
                _spawn_sync_thread()
                auto_sync_started = True
        except Exception as e:
            logger.warning("Auto-sync check failed: %s", e)

    return jsonify({"status": "ok", "backend": backend, "auto_sync_started": auto_sync_started})


@settings_bp.route("/api/settings/clear-history", methods=["POST"])
def settings_clear_history():
    rythmx_store.clear_history()
    return jsonify({"status": "ok"})


@settings_bp.route("/api/settings/reset-db", methods=["POST"])
def settings_reset_db():
    rythmx_store.reset_db()
    return jsonify({"status": "ok"})


@settings_bp.route("/api/settings/clear-image-cache", methods=["POST"])
def settings_clear_image_cache():
    rythmx_store.clear_image_cache()
    return jsonify({"status": "ok", "message": "Image cache cleared"})
