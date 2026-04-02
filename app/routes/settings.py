import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app import config
from app.clients import last_fm_client, plex_push, soulsync_api
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


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
        "navidrome_configured": bool(config.NAVIDROME_URL and config.NAVIDROME_USER and config.NAVIDROME_PASS),
        "soulsync_url": config.SOULSYNC_URL,
        "soulsync_db": config.SOULSYNC_DB,
        "soulsync_db_accessible": accessible,
        "spotify_configured": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
        "fanart_configured": bool(config.FANART_API_KEY),
        "library_platform": rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM,
        "library_accessible": accessible,
        "library_track_count": lr.get_track_count() if accessible else 0,
        "library_last_synced": rythmx_store.get_setting("library_last_synced"),
        "fetch_enabled": rythmx_store.get_setting("fetch_enabled", "0") == "1",
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
        return _verify_to_connected("navidrome")
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


@router.post("/settings/library-platform")
def settings_set_library_platform(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    platform = data.get("platform", "").lower()
    if platform not in {"plex", "navidrome", "jellyfin"}:
        return JSONResponse(
            {"status": "error", "message": f"Invalid platform: {platform}"}, status_code=400
        )

    old_platform = rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM
    rythmx_store.set_setting("library_platform", platform)

    if old_platform != platform:
        # Soft-delete all rows from the old platform so the new platform starts clean.
        # Enrichment data is preserved — COALESCE guards re-match by artist name on next sync.
        _soft_delete_platform_rows(old_platform)
        logger.info(
            "library_platform changed %s → %s; old rows tombstoned; resync triggered",
            old_platform, platform,
        )
        _trigger_background_sync()

    return {"status": "ok", "platform": platform, "resync_triggered": old_platform != platform}


def _soft_delete_platform_rows(platform: str) -> None:
    """Tombstone all lib_* rows for the given platform."""
    import sqlite3 as _sq
    try:
        conn = _sq.connect(config.RYTHMX_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE lib_tracks SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = ? AND removed_at IS NULL",
            (platform,),
        )
        conn.execute(
            "UPDATE lib_albums SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = ? AND removed_at IS NULL",
            (platform,),
        )
        conn.execute(
            "UPDATE lib_artists SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = ? AND removed_at IS NULL",
            (platform,),
        )
        conn.commit()
        conn.close()
        logger.info("_soft_delete_platform_rows: tombstoned all '%s' rows", platform)
    except Exception as exc:
        logger.error("_soft_delete_platform_rows failed for platform '%s': %s", platform, exc)


def _trigger_background_sync() -> None:
    """Start a background thread to sync the newly selected platform's library."""
    import threading as _threading

    def _run():
        try:
            from app.db import get_library_reader
            reader = get_library_reader()
            result = reader.sync_library()
            logger.info("Background sync after platform switch complete: %s", result)
        except Exception as exc:
            logger.error("Background sync after platform switch failed: %s", exc)

    t = _threading.Thread(target=_run, daemon=True, name="platform-switch-sync")
    t.start()


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


@router.post("/settings/fetch-enabled")
def settings_set_fetch_enabled(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    enabled = bool(data.get("enabled", False))
    rythmx_store.set_setting("fetch_enabled", "1" if enabled else "0")
    return {"status": "ok", "fetch_enabled": enabled}
