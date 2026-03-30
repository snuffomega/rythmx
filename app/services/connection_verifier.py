"""
connection_verifier.py — Per-service connection testing and verification state.

Tests connectivity to each configured external service and stores verification
timestamps in app_settings. The pipeline auto-unlocks once the required library
reader (Plex) is verified.

Reuses existing test_connection() functions from:
  - app.clients.plex_push (Plex)
  - app.clients.last_fm_client (Last.fm)
"""
import logging
from datetime import datetime

from app import config
from app.db import rythmx_store

logger = logging.getLogger(__name__)

def _get_services() -> list[tuple]:
    """Return service definitions based on the active library platform."""
    platform = config.LIBRARY_PLATFORM
    try:
        from app.db import rythmx_store
        saved = rythmx_store.get_setting("library_platform")
        if saved:
            platform = saved
    except Exception:
        pass

    services = []
    if platform == "navidrome":
        services.append(("navidrome", "Navidrome", True))
        services.append(("plex", "Plex", False))
    else:
        services.append(("plex", "Plex", True))
        services.append(("navidrome", "Navidrome", False))

    services += [
        ("spotify", "Spotify", False),
        ("lastfm", "Last.fm", False),
        ("fanart", "Fanart.tv", False),
        ("deezer", "Deezer", False),
    ]
    return services


def _verified_at_key(service: str) -> str:
    return f"{service}_verified_at"


def verify_service(service: str) -> dict:
    """Test a single service connection. Returns {status, service, ...}."""
    result = {"service": service, "required": False}

    if service == "plex":
        result["required"] = True
        from app.clients.plex_push import test_connection
        r = test_connection()
        result.update(r)

    elif service == "navidrome":
        result.update(_test_navidrome())

    elif service == "spotify":
        result.update(_test_spotify())

    elif service == "lastfm":
        from app.clients.last_fm_client import test_connection
        r = test_connection()
        result.update(r)

    elif service == "fanart":
        result.update(_test_fanart())

    elif service == "deezer":
        result.update(_test_deezer())

    else:
        return {"service": service, "status": "error", "message": f"Unknown service: {service}"}

    # Store verified_at on success
    if result.get("status") == "ok":
        rythmx_store.set_setting(_verified_at_key(service), datetime.utcnow().isoformat())
    else:
        # Clear verification on failure
        rythmx_store.set_setting(_verified_at_key(service), "")

    return result


def verify_all() -> dict:
    """Test all configured services. Returns aggregate status + per-service results."""
    results = {}
    all_ok = True
    required_ok = True

    for key, name, required in _get_services():
        r = verify_service(key)
        r["display_name"] = name
        results[key] = r

        if r.get("status") != "ok":
            all_ok = False
            if required:
                required_ok = False

    # Aggregate status
    if all_ok:
        status = "ok"
    elif required_ok:
        status = "partial"  # optional services failed, but pipeline can run
    else:
        status = "error"  # required service failed

    return {
        "status": status,
        "services": results,
        "pipeline_ready": required_ok,
    }


def get_verification_status() -> dict:
    """Return current verification state from DB (no live testing)."""
    settings = rythmx_store.get_all_settings()
    services = {}

    for key, name, required in _get_services():
        verified_at = settings.get(_verified_at_key(key), "")
        services[key] = {
            "service": key,
            "display_name": name,
            "required": required,
            "verified_at": verified_at if verified_at else None,
            "status": "verified" if verified_at else "unverified",
        }

    pipeline_ready = is_pipeline_ready()
    return {
        "services": services,
        "pipeline_ready": pipeline_ready,
    }


def is_pipeline_ready() -> bool:
    """Quick check: is the required library reader verified?"""
    platform = config.LIBRARY_PLATFORM
    try:
        saved = rythmx_store.get_setting("library_platform")
        if saved:
            platform = saved
    except Exception:
        pass
    required_service = "navidrome" if platform == "navidrome" else "plex"
    return bool(rythmx_store.get_setting(_verified_at_key(required_service)))


# ------------------------------------------------------------------
# Service-specific test functions
# ------------------------------------------------------------------

def _test_spotify() -> dict:
    """Test Spotify client credentials flow."""
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"status": "error", "message": "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must both be set"}
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
        # Light test: search for a well-known artist
        results = sp.search(q="artist:Radiohead", type="artist", limit=1)
        if results and results.get("artists", {}).get("items"):
            return {"status": "ok"}
        return {"status": "error", "message": "Spotify API returned no results"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _test_fanart() -> dict:
    """Test Fanart.tv API key with a known MBID."""
    if not config.FANART_API_KEY:
        return {"status": "not_configured", "message": "FANART_API_KEY not set"}
    try:
        import requests as req
        # Use Radiohead's MBID as a known-good test
        mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        resp = req.get(
            f"https://webservice.fanart.tv/v3/music/{mbid}",
            params={"api_key": config.FANART_API_KEY},
            timeout=10,
        )
        if resp.status_code == 200:
            return {"status": "ok"}
        if resp.status_code == 401:
            return {"status": "error", "message": "Invalid API key"}
        return {"status": "error", "message": f"Fanart.tv returned {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _test_deezer() -> dict:
    """Test Deezer public API (no key needed)."""
    try:
        import requests as req
        resp = req.get(
            "https://api.deezer.com/search/artist",
            params={"q": "Radiohead", "limit": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "error" not in data:
                return {"status": "ok"}
            return {"status": "error", "message": data["error"].get("message", "Unknown error")}
        return {"status": "error", "message": f"Deezer returned {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _test_navidrome() -> dict:
    """Test Navidrome connectivity using NavidromeClient.ping()."""
    url = rythmx_store.get_setting("navidrome_url") or config.NAVIDROME_URL
    user = rythmx_store.get_setting("navidrome_user") or config.NAVIDROME_USER
    password = rythmx_store.get_setting("navidrome_pass") or config.NAVIDROME_PASS

    if not url or not user or not password:
        return {
            "status": "not_configured",
            "message": "NAVIDROME_URL, NAVIDROME_USER, and NAVIDROME_PASS must be set",
        }
    try:
        from app.clients.navidrome_client import NavidromeClient
        client = NavidromeClient(url, user, password)
        if client.ping():
            return {"status": "ok"}
        return {"status": "error", "message": "Navidrome ping failed — check credentials"}
    except Exception as exc:
        # Never include URL or credentials in the message
        return {"status": "error", "message": f"Navidrome connection error: {type(exc).__name__}"}
