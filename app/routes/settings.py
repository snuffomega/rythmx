import logging
import threading
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app import config
from app.clients import last_fm_client, plex_push, soulsync_api
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])

# Track background thread state
_enrich_thread: threading.Thread | None = None
_enrich_lock = threading.Lock()
_spotify_enrich_thread: threading.Thread | None = None
_spotify_enrich_lock = threading.Lock()
_lastfm_tags_thread: threading.Thread | None = None
_lastfm_tags_lock = threading.Lock()
_deezer_bpm_thread: threading.Thread | None = None
_deezer_bpm_lock = threading.Lock()


@router.get("/settings")
def settings_get():
    from app.db import get_library_reader
    lr = get_library_reader()
    accessible = lr.is_db_accessible()
    return {
        "status": "ok",
        "lastfm_username": config.LASTFM_USERNAME,
        "lastfm_configured": bool(config.LASTFM_API_KEY and config.LASTFM_USERNAME),
        "plex_url": config.PLEX_URL,
        "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
        "soulsync_url": config.SOULSYNC_URL,
        "soulsync_db": config.SOULSYNC_DB,
        "soulsync_db_accessible": accessible,
        "spotify_configured": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
        "fanart_configured": bool(config.FANART_API_KEY),
        "library_platform": rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM,
        "library_accessible": accessible,
        "library_track_count": lr.get_track_count() if accessible else 0,
        "library_last_synced": rythmx_store.get_setting("library_last_synced"),
    }


def _verify_to_connected(service: str) -> dict:
    """Delegate to connection_verifier and return legacy {connected, message} shape."""
    from app.services.connection_verifier import verify_service
    result = verify_service(service)
    ok = result.get("status") == "ok"
    return {"connected": ok, "message": result.get("message") if not ok else None}


@router.post("/settings/test-lastfm")
def settings_test_lastfm():
    return _verify_to_connected("lastfm")


@router.post("/settings/test-plex")
def settings_test_plex():
    return _verify_to_connected("plex")


@router.post("/settings/test-soulsync")
def settings_test_soulsync():
    active_backend = rythmx_store.get_setting("library_platform") or "plex"

    if active_backend == "navidrome":
        return {"connected": False, "message": "Navidrome not yet implemented"}
    if active_backend == "jellyfin":
        return {"connected": False, "message": "Jellyfin not yet implemented"}
    if active_backend == "plex":
        import os as _os, sqlite3 as _sq
        db_path = config.RYTHMX_DB
        if not _os.path.exists(db_path):
            return {"connected": False,
                    "message": "Library DB not synced yet — run pipeline first"}
        try:
            with _sq.connect(db_path) as _c:
                tbl = _c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='lib_tracks'"
                ).fetchone()
                if not tbl:
                    return {"connected": False,
                            "message": "Library not synced yet — run pipeline first"}
                count = _c.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()[0]
            return {"connected": True, "message": f"{count:,} tracks indexed"}
        except Exception as e:
            return {"connected": False, "message": str(e)}
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
    return {"connected": ok, "message": msg}


@router.post("/settings/test-spotify")
def settings_test_spotify():
    return _verify_to_connected("spotify")


@router.post("/settings/test-fanart")
def settings_test_fanart():
    return _verify_to_connected("fanart")


# ------------------------------------------------------------------
# Connection verification (unified verify-all + per-service + status)
# ------------------------------------------------------------------

@router.post("/connections/verify")
def connections_verify_all():
    """Test all configured services and store verification timestamps."""
    from app.services.connection_verifier import verify_all
    return verify_all()


@router.post("/connections/verify/{service}")
def connections_verify_service(service: str):
    """Test a single service connection and store verification timestamp."""
    from app.services.connection_verifier import verify_service
    return verify_service(service)


@router.get("/connections/status")
def connections_status():
    """Return current verification state from DB (no live testing)."""
    from app.services.connection_verifier import get_verification_status
    return get_verification_status()


@router.get("/library/status")
def library_status():
    from app.services import library_service
    status = library_service.get_status()
    return {"status": "ok", **status}


@router.get("/library/enrich-status")
def library_enrich_status():
    from app.services import library_service
    global _enrich_thread
    status = library_service.get_status()
    running = _enrich_thread is not None and _enrich_thread.is_alive()
    return {"status": "ok", "enrich_running": running, **status}


@router.post("/library/enrich")
def library_enrich(data: Optional[dict[str, Any]] = Body(default=None)):
    global _enrich_thread
    with _enrich_lock:
        if _enrich_thread is not None and _enrich_thread.is_alive():
            return JSONResponse(
                {"status": "ok", "message": "Enrich already running"}, status_code=202
            )

        data = data or {}
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

    return JSONResponse({"status": "ok", "message": "Enrich started"}, status_code=202)


@router.get("/library/spotify-status")
def library_spotify_status():
    from app.services import library_service
    global _spotify_enrich_thread
    status = library_service.get_spotify_status()
    running = _spotify_enrich_thread is not None and _spotify_enrich_thread.is_alive()
    return {"status": "ok", "enrich_running": running, **status}


@router.post("/library/enrich-spotify")
def library_enrich_spotify(data: Optional[dict[str, Any]] = Body(default=None)):
    global _spotify_enrich_thread
    with _spotify_enrich_lock:
        if _spotify_enrich_thread is not None and _spotify_enrich_thread.is_alive():
            return JSONResponse(
                {"status": "ok", "message": "Spotify enrich already running"}, status_code=202
            )

        data = data or {}
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

    return JSONResponse(
        {"status": "ok", "message": "Spotify enrich started"}, status_code=202
    )


@router.get("/library/lastfm-tags-status")
def library_lastfm_tags_status():
    from app.services import library_service
    global _lastfm_tags_thread
    status = library_service.get_lastfm_tags_status()
    running = _lastfm_tags_thread is not None and _lastfm_tags_thread.is_alive()
    return {"status": "ok", "enrich_running": running, **status}


@router.post("/library/enrich-lastfm-tags")
def library_enrich_lastfm_tags(data: Optional[dict[str, Any]] = Body(default=None)):
    global _lastfm_tags_thread
    with _lastfm_tags_lock:
        if _lastfm_tags_thread is not None and _lastfm_tags_thread.is_alive():
            return JSONResponse(
                {"status": "ok", "message": "Last.fm tag enrich already running"}, status_code=202
            )

        data = data or {}
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

    return JSONResponse(
        {"status": "ok", "message": "Last.fm tag enrich started"}, status_code=202
    )


@router.get("/library/deezer-bpm-status")
def library_deezer_bpm_status():
    from app.services import library_service
    global _deezer_bpm_thread
    status = library_service.get_deezer_bpm_status()
    running = _deezer_bpm_thread is not None and _deezer_bpm_thread.is_alive()
    return {"status": "ok", "enrich_running": running, **status}


@router.post("/library/enrich-deezer-bpm")
def library_enrich_deezer_bpm(data: Optional[dict[str, Any]] = Body(default=None)):
    global _deezer_bpm_thread
    with _deezer_bpm_lock:
        if _deezer_bpm_thread is not None and _deezer_bpm_thread.is_alive():
            return JSONResponse(
                {"status": "ok", "message": "Deezer BPM enrich already running"}, status_code=202
            )

        data = data or {}
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

    return JSONResponse(
        {"status": "ok", "message": "Deezer BPM enrich started"}, status_code=202
    )


@router.post("/settings/library-platform")
def settings_set_library_platform(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    platform = data.get("platform", "").lower()
    if platform not in {"plex", "navidrome", "jellyfin"}:
        return JSONResponse(
            {"status": "error", "message": f"Invalid platform: {platform}"}, status_code=400
        )
    rythmx_store.set_setting("library_platform", platform)
    return {"status": "ok", "platform": platform}


@router.post("/settings/clear-history")
def settings_clear_history():
    rythmx_store.clear_history()
    return {"status": "ok"}


@router.post("/settings/reset-db")
def settings_reset_db():
    rythmx_store.reset_db()
    return {"status": "ok"}


@router.get("/settings/api-key")
def settings_get_api_key():
    """Return the current API key (authenticated — requires X-Api-Key header)."""
    key = rythmx_store.get_api_key()
    return {"status": "ok", "api_key": key}


@router.post("/settings/regenerate-api-key")
def settings_regenerate_api_key():
    """Generate and persist a new API key. The caller must update their stored key."""
    new_key = rythmx_store.generate_new_api_key()
    logger.info("API key regenerated")
    return {"status": "ok", "api_key": new_key}
