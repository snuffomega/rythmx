import logging
import os
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
    from app.db import soulsync_reader as _ss_reader

    lr = get_library_reader()
    accessible = lr.is_db_accessible()

    soulsync_url = (os.environ.get("SOULSYNC_URL", "") or "").strip() or None
    soulsync_db = (os.environ.get("SOULSYNC_DB", "") or "").strip() or None
    soulsync_configured = bool(soulsync_url or soulsync_db)
    soulsync_db_accessible = _ss_reader.is_db_accessible() if soulsync_configured else False

    return {
        "status": "ok",
        "lastfm_username": config.LASTFM_USERNAME,
        "lastfm_configured": bool(config.LASTFM_API_KEY and config.LASTFM_USERNAME),
        "plex_url": config.PLEX_URL,
        "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
        "navidrome_configured": bool(config.NAVIDROME_URL and config.NAVIDROME_USER and config.NAVIDROME_PASS),
        "soulsync_url": soulsync_url,
        "soulsync_db": soulsync_db,
        "soulsync_db_accessible": soulsync_db_accessible,
        "spotify_configured": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
        "fanart_configured": bool(config.FANART_API_KEY),
        "library_platform": rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM,
        "library_accessible": accessible,
        "library_track_count": lr.get_track_count() if accessible else 0,
        "library_last_synced": rythmx_store.get_setting("library_last_synced"),
        "fetch_enabled": rythmx_store.get_setting("fetch_enabled", "0") == "1",
        "catalog_primary": config.CATALOG_PRIMARY,
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
    try:
        with rythmx_store._connect() as conn:
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


@router.get("/settings/mobile-pairing")
def settings_mobile_pairing():
    """
    Returns the API key and a best-effort LAN base URL for mobile app pairing.
    Use api_base + X-Api-Key header to configure the React Native app.
    The lan_ip is detected from the default outbound interface — substitute your
    device's actual LAN IP if it differs.
    """
    import socket

    key = rythmx_store.get_api_key()

    # UDP connect trick: no packets sent, but the OS selects the correct interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
    except Exception:
        lan_ip = "127.0.0.1"

    port = config.RYTHMX_PORT
    api_base = f"http://{lan_ip}:{port}/api/v1"

    return {
        "status": "ok",
        "api_key": key,
        "api_base": api_base,
        "lan_ip": lan_ip,
        "port": port,
    }


# ---------------------------------------------------------------------------
# Plugin management — Integrations UI (Lidarr-style: drop file → configure in UI)
# ---------------------------------------------------------------------------

@router.get("/settings/plugins")
def settings_plugins_list():
    """
    Returns all discovered plugins, their config schemas, current config values, and
    per-slot enabled state. Used by the Settings → Integrations UI.
    """
    from app.plugins import get_plugin_catalog

    catalog = get_plugin_catalog()
    slot_config = rythmx_store.get_all_plugin_slot_config()
    plugin_settings_map = rythmx_store.get_all_plugin_settings()

    plugins = []
    for name, meta in catalog.items():
        config_schema = meta.get("config_schema") or []
        # Return config values — mask password fields so secrets never leave the server
        config_values: dict[str, str] = {}
        for field in config_schema:
            key = field.get("key", "")
            if not key:
                continue
            # DB value takes precedence over env
            raw = plugin_settings_map.get(name, {}).get(key) or os.environ.get(key, "")
            config_values[key] = "***" if field.get("type") == "password" and raw else raw

        slots_enabled: dict[str, bool] = {
            slot: slot_config.get((name, slot), True)
            for slot in (meta.get("slots") or [])
        }

        plugins.append({
            "name": name,
            "version": meta.get("version"),
            "description": meta.get("description"),
            "slots": meta.get("slots") or [],
            "active_slots": meta.get("active_slots") or [],
            "slots_enabled": slots_enabled,
            "config_schema": config_schema,
            "config_values": config_values,
            "capabilities": meta.get("capabilities") or {},
        })

    return {"status": "ok", "plugins": plugins}


@router.patch("/settings/plugins/{name}")
def settings_plugins_update(name: str, data: Optional[dict[str, Any]] = Body(default=None)):
    """
    Save plugin config values and per-slot enable/disable state, then hot-reload.
    Body: {"config": {"KEY": "value", ...}, "slots": {"downloader": true, ...}}
    """
    from app.plugins import get_plugin_catalog, reload_plugins

    catalog = get_plugin_catalog()
    if name not in catalog:
        return JSONResponse(
            {"status": "error", "message": f"Plugin '{name}' not found"},
            status_code=404,
        )

    payload = data if isinstance(data, dict) else {}

    # Save config values to app_settings
    config_updates = payload.get("config")
    if isinstance(config_updates, dict):
        for key, value in config_updates.items():
            if value is not None:
                rythmx_store.set_setting(f"plugin.{name}.{key}", str(value))

    # Save slot enable/disable state
    slots_updates = payload.get("slots")
    if isinstance(slots_updates, dict):
        for slot, enabled in slots_updates.items():
            rythmx_store.set_plugin_slot_enabled(name, slot, bool(enabled))

    # Reload all plugins with the updated DB config
    reload_plugins()

    return {"status": "ok", "message": f"Plugin '{name}' config saved and reloaded"}


@router.post("/settings/plugins/{name}/test")
def settings_plugins_test(name: str):
    """
    Call test_connection() on the named plugin if it occupies the downloader slot.
    Returns the plugin's own result dict.
    """
    from app.plugins import get_plugin_catalog, get_downloader

    catalog = get_plugin_catalog()
    if name not in catalog:
        return JSONResponse(
            {"status": "error", "message": f"Plugin '{name}' not found"},
            status_code=404,
        )

    if "downloader" not in (catalog[name].get("active_slots") or []):
        return JSONResponse(
            {"status": "error", "message": f"Plugin '{name}' downloader slot is not active"},
            status_code=400,
        )

    dl = get_downloader()
    if getattr(dl, "name", None) != name:
        return JSONResponse(
            {"status": "error", "message": f"Plugin '{name}' is not the current active downloader"},
            status_code=400,
        )

    try:
        result = dl.test_connection()
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.warning("Plugin %s test_connection raised: %s", name, exc)
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=502,
        )


@router.post("/settings/plugins/reload")
def settings_plugins_reload():
    """Hot-reload all plugins from disk using current DB config (slot config + settings)."""
    from app.plugins import reload_plugins
    reload_plugins()
    return {"status": "ok", "message": "Plugins reloaded"}
