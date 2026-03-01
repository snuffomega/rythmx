"""
playlist_importer.py — import external playlists into rythmx.

Supported sources:
  - Spotify  (public playlists — requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)
  - Last.fm  (public playlists — requires LASTFM_API_KEY)
  - Deezer   (public playlists — no credentials required)

Private playlists / OAuth flows are not supported; a clear error is returned.

Future (deferred):
  - Apple Music (requires Apple Developer .p8 key + team/key IDs)
"""
import re
import logging
import urllib.request
import urllib.parse
import json
from app import config

logger = logging.getLogger(__name__)


def _extract_spotify_playlist_id(url: str) -> str | None:
    """Extract Spotify playlist ID from a URL or bare ID string."""
    # Handles: https://open.spotify.com/playlist/37i9dQZF...
    #          spotify:playlist:37i9dQZF...
    #          37i9dQZF... (bare ID)
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    # Bare ID: 22 alphanumeric chars
    if re.fullmatch(r"[A-Za-z0-9]{22}", url.strip()):
        return url.strip()
    return None


def import_from_spotify(playlist_url: str) -> dict:
    """
    Fetch tracks from a public Spotify playlist and match against the SoulSync library.

    Returns:
        {
            "status": "ok",
            "name": str,            # Spotify playlist name
            "tracks": [...],        # one entry per track (see below)
            "track_count": int,
            "owned_count": int,
        }

    Each track entry:
        {
            "track_name": str,
            "artist_name": str,
            "album_name": str,
            "spotify_track_id": str,
            "is_owned": bool,
            "plex_rating_key": str | None,
        }

    On failure returns {"status": "error", "message": str}.
    """
    from app.db import get_library_reader
    soulsync_reader = get_library_reader()

    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"status": "error", "message": "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not configured"}

    playlist_id = _extract_spotify_playlist_id(playlist_url)
    if not playlist_id:
        return {"status": "error", "message": f"Could not extract a Spotify playlist ID from: {playlist_url!r}"}

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
    except Exception as e:
        logger.error("Spotify client init failed: %s", e)
        return {"status": "error", "message": f"Spotify client init failed: {e}"}

    # Fetch playlist metadata
    try:
        meta = sp.playlist(playlist_id, fields="name,tracks.total")
        playlist_name = meta.get("name", "Imported Playlist")
    except Exception as e:
        msg = str(e)
        if "403" in msg or "404" in msg:
            return {"status": "error",
                    "message": "Playlist not accessible — make sure it is public on Spotify"}
        logger.error("Spotify playlist fetch failed for %s: %s", playlist_id, e)
        return {"status": "error", "message": f"Could not fetch playlist: {e}"}

    # Fetch all tracks (paginated)
    raw_tracks = []
    offset = 0
    while True:
        try:
            resp = sp.playlist_tracks(playlist_id, limit=100, offset=offset,
                                      fields="items(track(id,name,artists,album(name))),next")
        except Exception as e:
            logger.error("Spotify playlist_tracks failed at offset %d: %s", offset, e)
            break
        items = resp.get("items") or []
        for item in items:
            track = item.get("track")
            if not track:
                continue
            artists = track.get("artists") or []
            raw_tracks.append({
                "spotify_track_id": track.get("id", ""),
                "track_name": track.get("name", ""),
                "artist_name": artists[0]["name"] if artists else "",
                "album_name": track.get("album", {}).get("name", ""),
            })
        if not resp.get("next"):
            break
        offset += 100

    logger.info("Spotify import: fetched %d tracks from playlist '%s'", len(raw_tracks), playlist_name)

    # Match each track against SoulSync library
    tracks = []
    owned_count = 0
    for rt in raw_tracks:
        plex_key = None

        # Tier 1: exact Spotify track ID
        if rt["spotify_track_id"]:
            plex_key = soulsync_reader.check_owned_exact(rt["spotify_track_id"])

        # Tier 2: artist name + track title text match
        if not plex_key:
            plex_key = soulsync_reader.find_track_by_name(rt["artist_name"], rt["track_name"])

        is_owned = plex_key is not None
        if is_owned:
            owned_count += 1

        tracks.append({
            "track_name": rt["track_name"],
            "artist_name": rt["artist_name"],
            "album_name": rt["album_name"],
            "spotify_track_id": rt["spotify_track_id"],
            "is_owned": is_owned,
            "plex_rating_key": plex_key,
        })

    logger.info(
        "Spotify import match: %d/%d tracks owned in library",
        owned_count, len(tracks),
    )

    return {
        "status": "ok",
        "name": playlist_name,
        "tracks": tracks,
        "track_count": len(tracks),
        "owned_count": owned_count,
    }


# ---------------------------------------------------------------------------
# Last.fm playlist import
# ---------------------------------------------------------------------------

def _extract_lastfm_playlist_parts(url: str) -> tuple[str, str] | None:
    """Extract (username, playlist_id) from a Last.fm playlist URL.

    Handles:
      https://www.last.fm/user/{username}/playlists/{id}
      https://www.last.fm/user/{username}/playlists/{id}.jspf
    Returns None if the URL doesn't match.
    """
    m = re.search(r"last\.fm/user/([^/]+)/playlists/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def import_from_lastfm(playlist_url: str) -> dict:
    """
    Fetch tracks from a public Last.fm playlist and match against the SoulSync library.

    Uses the JSPF (JSON Shareable Playlist Format) endpoint — the deprecated
    playlist.fetch API is not used. URL format:
      https://www.last.fm/user/{username}/playlists/{id}

    Returns same shape as import_from_spotify().
    On failure returns {"status": "error", "message": str}.
    """
    from app.db import get_library_reader
    soulsync_reader = get_library_reader()

    parts = _extract_lastfm_playlist_parts(playlist_url)
    if not parts:
        return {
            "status": "error",
            "message": (
                "Could not extract username and playlist ID from URL. "
                f"Expected format: https://www.last.fm/user/{{username}}/playlists/{{id}} "
                f"(got: {playlist_url!r})"
            ),
        }

    username, playlist_id = parts
    jspf_url = f"https://www.last.fm/user/{username}/playlists/{playlist_id}.jspf"

    try:
        req = urllib.request.Request(jspf_url, headers={"User-Agent": "rythmx/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("Last.fm JSPF fetch failed for %s/%s: %s", username, playlist_id, e)
        return {"status": "error", "message": f"Could not fetch Last.fm playlist: {e}"}

    playlist_data = data.get("playlist") or {}
    if not playlist_data:
        return {
            "status": "error",
            "message": "Playlist not found or private — make sure the URL is correct and the playlist is public",
        }

    playlist_name = playlist_data.get("title") or f"Last.fm Playlist {playlist_id}"

    items = playlist_data.get("track") or []
    # Last.fm returns a dict (not list) when there is only one track
    if isinstance(items, dict):
        items = [items]

    raw_tracks = []
    for item in items:
        # identifier is a LIST of strings in JSPF (may also be a bare string — handle both)
        identifiers = item.get("identifier") or []
        if isinstance(identifiers, str):
            identifiers = [identifiers]

        spotify_id = None
        for ident in identifiers:
            m = re.search(r"spotify:track:([A-Za-z0-9]+)", ident)
            if m:
                spotify_id = m.group(1)
                break
            m = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", ident)
            if m:
                spotify_id = m.group(1)
                break

        raw_tracks.append({
            "spotify_track_id": spotify_id or "",
            "track_name": item.get("title", ""),
            "artist_name": item.get("creator", ""),
            "album_name": item.get("album", ""),
        })

    logger.info("Last.fm import: fetched %d tracks from playlist '%s'", len(raw_tracks), playlist_name)

    tracks = []
    owned_count = 0
    for rt in raw_tracks:
        plex_key = None

        # Tier 1: exact Spotify track ID (if embedded in JSPF identifier)
        if rt["spotify_track_id"]:
            plex_key = soulsync_reader.check_owned_exact(rt["spotify_track_id"])

        # Tier 2: normalized artist + title text match (handles unicode apostrophes)
        if not plex_key:
            plex_key = soulsync_reader.find_track_by_name(rt["artist_name"], rt["track_name"])

        is_owned = plex_key is not None
        if is_owned:
            owned_count += 1

        tracks.append({
            "track_name": rt["track_name"],
            "artist_name": rt["artist_name"],
            "album_name": rt["album_name"],
            "spotify_track_id": rt["spotify_track_id"],
            "is_owned": is_owned,
            "plex_rating_key": plex_key,
        })

    logger.info(
        "Last.fm import match: %d/%d tracks owned in library",
        owned_count, len(tracks),
    )

    return {
        "status": "ok",
        "name": playlist_name,
        "tracks": tracks,
        "track_count": len(tracks),
        "owned_count": owned_count,
    }


# ---------------------------------------------------------------------------
# Deezer playlist import
# ---------------------------------------------------------------------------

def _extract_deezer_playlist_id(url: str) -> str | None:
    """Extract Deezer playlist ID from a URL or bare ID."""
    m = re.search(r"deezer\.com/(?:\w+/)?playlist/(\d+)", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", url.strip()):
        return url.strip()
    return None


def import_from_deezer(playlist_url: str) -> dict:
    """
    Fetch tracks from a public Deezer playlist and match against the SoulSync library.

    No credentials required — uses the public Deezer API.
    Returns same shape as import_from_spotify().
    On failure returns {"status": "error", "message": str}.
    """
    from app.db import get_library_reader
    soulsync_reader = get_library_reader()

    playlist_id = _extract_deezer_playlist_id(playlist_url)
    if not playlist_id:
        return {"status": "error", "message": f"Could not extract a Deezer playlist ID from: {playlist_url!r}"}

    # Fetch playlist metadata + first page of tracks
    try:
        with urllib.request.urlopen(
            f"https://api.deezer.com/playlist/{playlist_id}", timeout=15
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("Deezer playlist fetch failed for %s: %s", playlist_id, e)
        return {"status": "error", "message": f"Could not fetch Deezer playlist: {e}"}

    if "error" in data:
        err = data["error"]
        msg = err.get("message", "Unknown Deezer error")
        code = err.get("code", "")
        if code in (4, 100, 800):
            return {"status": "error", "message": "Deezer playlist not accessible — make sure it is public"}
        return {"status": "error", "message": f"Deezer error: {msg}"}

    playlist_name = data.get("title") or f"Deezer Playlist {playlist_id}"

    # Paginate all tracks
    raw_tracks = []
    tracks_data = data.get("tracks", {})
    page_items = tracks_data.get("data") or []
    raw_tracks.extend(page_items)
    next_url = tracks_data.get("next")

    while next_url:
        try:
            with urllib.request.urlopen(next_url, timeout=15) as resp:
                page = json.loads(resp.read().decode("utf-8"))
            raw_tracks.extend(page.get("data") or [])
            next_url = page.get("next")
        except Exception as e:
            logger.warning("Deezer pagination failed at %s: %s", next_url, e)
            break

    logger.info("Deezer import: fetched %d tracks from playlist '%s'", len(raw_tracks), playlist_name)

    tracks = []
    owned_count = 0
    for item in raw_tracks:
        artist_name = (item.get("artist") or {}).get("name", "")
        track_name = item.get("title", "")
        album_name = (item.get("album") or {}).get("title", "")
        deezer_track_id = str(item.get("id", ""))

        plex_key = None

        # Tier 1: exact Deezer track ID match (fast, no text ambiguity)
        # Only resolves when SoulSync has indexed the track via Deezer catalog (deezer_id populated).
        if deezer_track_id:
            plex_key = soulsync_reader.check_owned_deezer(deezer_track_id)

        # Tier 2: normalized artist + title text match (handles unicode apostrophes)
        if not plex_key:
            plex_key = soulsync_reader.find_track_by_name(artist_name, track_name)

        is_owned = plex_key is not None
        if is_owned:
            owned_count += 1

        tracks.append({
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
            "spotify_track_id": "",
            "is_owned": is_owned,
            "plex_rating_key": plex_key,
        })

    logger.info(
        "Deezer import match: %d/%d tracks owned in library",
        owned_count, len(tracks),
    )

    return {
        "status": "ok",
        "name": playlist_name,
        "tracks": tracks,
        "track_count": len(tracks),
        "owned_count": owned_count,
    }
