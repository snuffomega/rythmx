"""
plex_push.py — create or update a Plex playlist using ratingKey values.

ratingKey values come from SoulSync's tracks.id column.
PLEX_TOKEN is never logged.
"""
import requests
import logging
from app import config

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "X-Plex-Token": config.PLEX_TOKEN,
        "Accept": "application/json",
    }


def _get_machine_id() -> str | None:
    """Get the Plex server machine identifier (needed for some API calls)."""
    try:
        resp = requests.get(f"{config.PLEX_URL}/", headers=_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("MediaContainer", {}).get("machineIdentifier")
    except Exception as e:
        logger.error("Failed to get Plex machine ID: %s", e)
        return None


def get_playlists() -> list[dict]:
    """Return all playlists from Plex."""
    try:
        resp = requests.get(f"{config.PLEX_URL}/playlists", headers=_headers(), timeout=10)
        resp.raise_for_status()
        playlists = resp.json().get("MediaContainer", {}).get("Metadata", [])
        return playlists
    except Exception as e:
        logger.error("Failed to fetch Plex playlists: %s", e)
        return []


def find_playlist(name: str) -> dict | None:
    """Find a playlist by name. Returns the playlist dict or None."""
    for pl in get_playlists():
        if pl.get("title") == name:
            return pl
    return None


def create_playlist(name: str, rating_keys: list[str]) -> dict | None:
    """
    Create a new Plex playlist with the given ratingKey values.
    Returns the created playlist dict or None on failure.
    """
    if not rating_keys:
        logger.warning("create_playlist called with empty rating_keys")
        return None

    machine_id = _get_machine_id()
    if not machine_id:
        return None

    # Build the URI list Plex expects
    uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{','.join(rating_keys)}"

    try:
        resp = requests.post(
            f"{config.PLEX_URL}/playlists",
            headers=_headers(),
            params={
                "title": name,
                "type": "audio",
                "smart": 0,
                "uri": uri,
            },
            timeout=10,
        )
        resp.raise_for_status()
        playlists = resp.json().get("MediaContainer", {}).get("Metadata", [])
        created = playlists[0] if playlists else None
        if created:
            logger.info("Created Plex playlist '%s' with %d tracks", name, len(rating_keys))
        return created
    except Exception as e:
        logger.error("Failed to create Plex playlist '%s': %s", name, e)
        return None


def update_playlist(playlist_key: str, rating_keys: list[str]) -> bool:
    """
    Replace all items in an existing Plex playlist.
    playlist_key — the ratingKey of the playlist itself (from find_playlist).
    """
    if not rating_keys:
        return False

    try:
        # First, clear existing items
        resp = requests.delete(
            f"{config.PLEX_URL}/playlists/{playlist_key}/items",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()

        # Then add new items
        machine_id = _get_machine_id()
        if not machine_id:
            return False

        uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{','.join(rating_keys)}"
        resp = requests.put(
            f"{config.PLEX_URL}/playlists/{playlist_key}/items",
            headers=_headers(),
            params={"uri": uri},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Updated Plex playlist (key=%s) with %d tracks", playlist_key, len(rating_keys))
        return True
    except Exception as e:
        logger.error("Failed to update Plex playlist (key=%s): %s", playlist_key, e)
        return False


def create_or_update_playlist(name: str, rating_keys: list[str]) -> str | None:
    """
    Create or update a named Plex playlist. Returns the playlist ratingKey or None.
    This is the main entry point for cruise control playlist push.
    """
    if not config.PLEX_URL or not config.PLEX_TOKEN:
        logger.warning("Plex not configured — skipping playlist push")
        return None

    existing = find_playlist(name)
    if existing:
        key = existing.get("ratingKey")
        success = update_playlist(key, rating_keys)
        return key if success else None
    else:
        created = create_playlist(name, rating_keys)
        return created.get("ratingKey") if created else None


def test_connection() -> dict:
    """
    Verify Plex credentials. Returns {status, server_name} or {status, error}.
    Never logs PLEX_TOKEN.
    """
    if not config.PLEX_URL or not config.PLEX_TOKEN:
        return {"status": "error", "message": "PLEX_URL and PLEX_TOKEN must both be set"}

    try:
        resp = requests.get(f"{config.PLEX_URL}/", headers=_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        name = data.get("MediaContainer", {}).get("friendlyName", "Unknown")
        return {"status": "ok", "server_name": name}
    except Exception as e:
        return {"status": "error", "message": str(e)}
