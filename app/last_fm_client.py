"""
last_fm_client.py — Last.fm API client.

Docs: https://www.last.fm/api
Auth: API key only (no OAuth needed for read operations).
IMPORTANT: api_key is never logged.
"""
import requests
import logging
from app import config

logger = logging.getLogger(__name__)

BASE_URL = "https://ws.audioscrobbler.com/2.0/"

VALID_PERIODS = ("overall", "12month", "6month", "3month", "1month", "7day")


def _get(method: str, extra_params: dict = None) -> dict | None:
    """
    Make a GET request to the Last.fm API.
    Raises on missing API key. Never logs the key.
    """
    if not config.LASTFM_API_KEY:
        logger.warning("LASTFM_API_KEY not set — Last.fm calls disabled")
        return None

    params = {
        "method": method,
        "api_key": config.LASTFM_API_KEY,
        "format": "json",
    }
    if extra_params:
        params.update(extra_params)

    try:
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.error("Last.fm API error %s: %s", data.get("error"), data.get("message"))
            return None
        return data
    except requests.RequestException as e:
        logger.error("Last.fm request failed (%s): %s", method, e)
        return None


def get_top_artists(period: str = "6month", limit: int = 200) -> dict:
    """
    Returns {artist_name: play_count} for the configured user.
    period: one of overall / 12month / 6month / 3month / 1month / 7day
    """
    if period not in VALID_PERIODS:
        period = "6month"

    data = _get("user.getTopArtists", {
        "user": config.LASTFM_USERNAME,
        "period": period,
        "limit": limit,
    })
    if not data:
        return {}

    result = {}
    for artist in data.get("topartists", {}).get("artist", []):
        name = artist.get("name", "")
        playcount = int(artist.get("playcount", 0))
        result[name] = playcount

    logger.debug("Last.fm top artists fetched: %d (period=%s)", len(result), period)
    return result


def get_top_tracks(period: str = "6month", limit: int = 200) -> list[dict]:
    """
    Returns a list of top tracks: [{name, artist, playcount}, ...]
    """
    if period not in VALID_PERIODS:
        period = "6month"

    data = _get("user.getTopTracks", {
        "user": config.LASTFM_USERNAME,
        "period": period,
        "limit": limit,
    })
    if not data:
        return []

    tracks = []
    for t in data.get("toptracks", {}).get("track", []):
        tracks.append({
            "name": t.get("name", ""),
            "artist": t.get("artist", {}).get("name", ""),
            "playcount": int(t.get("playcount", 0)),
        })

    logger.debug("Last.fm top tracks fetched: %d (period=%s)", len(tracks), period)
    return tracks


def get_top_albums(period: str = "6month", limit: int = 200) -> list[dict]:
    """
    Returns a list of top albums: [{artist, title, playcount}, ...]
    period: one of overall / 12month / 6month / 3month / 1month / 7day
    """
    if period not in VALID_PERIODS:
        period = "6month"

    data = _get("user.getTopAlbums", {
        "user": config.LASTFM_USERNAME,
        "period": period,
        "limit": limit,
    })
    if not data:
        return []

    albums = []
    for a in data.get("topalbums", {}).get("album", []):
        albums.append({
            "artist": a.get("artist", {}).get("name", ""),
            "title": a.get("name", ""),
            "playcount": int(a.get("playcount", 0)),
        })

    logger.debug("Last.fm top albums fetched: %d (period=%s)", len(albums), period)
    return albums


def get_loved_tracks(limit: int = 500) -> list[dict]:
    """
    Returns a list of loved tracks: [{name, artist}, ...]
    """
    data = _get("user.getLovedTracks", {
        "user": config.LASTFM_USERNAME,
        "limit": limit,
    })
    if not data:
        return []

    tracks = []
    for t in data.get("lovedtracks", {}).get("track", []):
        tracks.append({
            "name": t.get("name", ""),
            "artist": t.get("artist", {}).get("name", ""),
        })

    logger.debug("Last.fm loved tracks fetched: %d", len(tracks))
    return tracks


def get_loved_artist_names(limit: int = 500) -> set:
    """Returns a set of artist names that appear in loved tracks. For scoring."""
    return {t["artist"] for t in get_loved_tracks(limit=limit) if t.get("artist")}


def get_recent_tracks(limit: int = 200) -> list[dict]:
    """
    Returns the user's recent scrobbles: [{name, artist, timestamp}, ...]
    """
    data = _get("user.getRecentTracks", {
        "user": config.LASTFM_USERNAME,
        "limit": limit,
    })
    if not data:
        return []

    tracks = []
    for t in data.get("recenttracks", {}).get("track", []):
        tracks.append({
            "name": t.get("name", ""),
            "artist": t.get("artist", {}).get("#text", ""),
            "timestamp": t.get("date", {}).get("uts"),
        })

    return tracks


def get_similar_artists(artist_name: str, limit: int = 20) -> list[dict]:
    """
    Returns similar artists from Last.fm: [{name, match_score}, ...]
    Supplement to SoulSync's music-map.com similar_artists table.
    """
    data = _get("artist.getSimilar", {
        "artist": artist_name,
        "limit": limit,
        "autocorrect": 1,
    })
    if not data:
        return []

    results = []
    for a in data.get("similarartists", {}).get("artist", []):
        results.append({
            "name": a.get("name", ""),
            "match": float(a.get("match", 0)),
        })

    return results


def get_artist_top_tracks(artist_name: str, limit: int = 10) -> list[str]:
    """
    Return top track title strings for a Last.fm artist name.
    Uses artist.getTopTracks — public endpoint, no user auth required.
    Returns raw (un-normalized) title strings — caller normalizes.
    Returns empty list on error or missing API key.
    """
    data = _get("artist.getTopTracks", {
        "artist": artist_name,
        "limit": limit,
        "autocorrect": 1,
    })
    if not data:
        return []

    tracks = data.get("toptracks", {}).get("track") or []
    # Last.fm returns a dict (not list) when there is only one track
    if isinstance(tracks, dict):
        tracks = [tracks]

    titles = [t.get("name", "") for t in tracks if t.get("name")]
    logger.debug("Last.fm artist top tracks for '%s': %d", artist_name, len(titles))
    return titles


def test_connection() -> dict:
    """
    Verify Last.fm credentials work. Returns {status, username} or {status, error}.
    Does not log the API key.
    """
    if not config.LASTFM_API_KEY or not config.LASTFM_USERNAME:
        return {"status": "error", "message": "LASTFM_API_KEY and LASTFM_USERNAME must both be set"}

    data = _get("user.getInfo", {"user": config.LASTFM_USERNAME})
    if not data:
        return {"status": "error", "message": "Could not reach Last.fm API or invalid credentials"}

    user = data.get("user", {})
    return {
        "status": "ok",
        "username": user.get("name"),
        "playcount": user.get("playcount"),
        "registered": user.get("registered", {}).get("#text"),
    }
