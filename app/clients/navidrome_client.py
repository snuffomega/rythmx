"""
navidrome_client.py — Subsonic/OpenSubsonic HTTP client for Navidrome.

Uses token auth mode: t=md5(password+salt), s=random_salt.
Never sends plaintext password in requests.

API reference:
  https://www.navidrome.org/docs/developers/subsonic-api/
  https://opensubsonic.netlify.app/docs/
"""
import hashlib
import logging
import secrets
import requests

logger = logging.getLogger(__name__)

_CLIENT_NAME = "rythmx"
_API_VERSION = "1.16.1"


class NavidromeError(Exception):
    """Raised on Subsonic API failure or HTTP error. Never contains credentials."""


class NavidromeClient:
    """Thin HTTP wrapper for the Subsonic/OpenSubsonic API."""

    def __init__(self, base_url: str, username: str, password: str):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        self._session.headers["User-Agent"] = f"{_CLIENT_NAME}/1.0"

    def _auth_params(self) -> dict:
        """Return token-mode auth params. Generates a fresh salt each call."""
        salt = secrets.token_hex(8)
        token = hashlib.md5((self._password + salt).encode()).hexdigest()
        return {
            "u": self._username,
            "t": token,
            "s": salt,
            "v": _API_VERSION,
            "c": _CLIENT_NAME,
            "f": "json",
        }

    def _get(self, endpoint: str, **params) -> dict:
        """Make a GET request to /rest/{endpoint}. Returns the subsonic-response body.

        Raises NavidromeError on HTTP error or Subsonic status=failed.
        Never includes credentials in error messages.
        """
        url = f"{self._base_url}/rest/{endpoint}"
        all_params = {**params, **self._auth_params()}
        try:
            resp = self._session.get(url, params=all_params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise NavidromeError(f"HTTP error calling {endpoint}: {type(exc).__name__}") from exc

        try:
            data = resp.json().get("subsonic-response", {})
        except Exception as exc:
            raise NavidromeError(f"Invalid JSON response from {endpoint}: {type(exc).__name__}") from exc

        if data.get("status") != "ok":
            error = data.get("error", {})
            # Include only the numeric error code — never echo server messages
            # that may contain credential hints (e.g. "Wrong username or password").
            raise NavidromeError(
                f"Subsonic API error (code={error.get('code', '?')}) on {endpoint}"
            )
        return data

    def ping(self) -> bool:
        """Return True if server is reachable and credentials are valid."""
        try:
            self._get("ping")
            return True
        except NavidromeError as exc:
            logger.debug("Navidrome ping failed: %s", exc)
            return False

    def get_music_folders(self) -> list[dict]:
        """Return list of configured music folders."""
        data = self._get("getMusicFolders")
        return data.get("musicFolders", {}).get("musicFolder", [])

    def get_artists(self) -> list[dict]:
        """Return flat list of all artists (from all music folders)."""
        data = self._get("getArtists")
        artists = []
        for index_entry in data.get("artists", {}).get("index", []):
            for artist in index_entry.get("artist", []):
                artists.append(artist)
        return artists

    def get_artist(self, artist_id: str) -> dict:
        """Return artist detail including album list."""
        data = self._get("getArtist", id=artist_id)
        return data.get("artist", {})

    def get_album(self, album_id: str) -> dict:
        """Return album detail including song list with OpenSubsonic fields."""
        data = self._get("getAlbum", id=album_id)
        return data.get("album", {})

    def search3(self, query: str, artist_count: int = 5, album_count: int = 0,
                song_count: int = 10) -> dict:
        """Search for artists, albums, and songs."""
        data = self._get(
            "search3",
            query=query,
            artistCount=artist_count,
            albumCount=album_count,
            songCount=song_count,
        )
        return data.get("searchResult3", {})

    def scrobble(self, song_id: str, time_ms: int | None = None,
                 submission: bool = True) -> None:
        """Scrobble a track play to Navidrome (which relays to Last.fm/ListenBrainz)."""
        params = {"id": song_id, "submission": "true" if submission else "false"}
        if time_ms is not None:
            params["time"] = time_ms
        self._get("scrobble", **params)

    def create_playlist(self, name: str, song_ids: list[str]) -> dict:
        """Create a new playlist. Returns the created PlaylistWithSongs dict."""
        params = {"name": name}
        # Subsonic allows repeated 'songId' params
        # requests handles list values as repeated params
        data = self._get("createPlaylist", **params, songId=song_ids)
        return data.get("playlist", {})

    def update_playlist(self, playlist_id: str, song_ids_to_add: list[str]) -> None:
        """Append tracks to an existing playlist."""
        self._get("updatePlaylist", playlistId=playlist_id, songIdToAdd=song_ids_to_add)

    def get_playlists(self) -> list[dict]:
        """Return all playlists for the current user."""
        data = self._get("getPlaylists")
        return data.get("playlists", {}).get("playlist", [])
