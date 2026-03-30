"""
navidrome_push.py — Create or update playlists on a Navidrome server.

Track IDs passed in are Navidrome song IDs (as stored in lib_tracks.id
when source_platform='navidrome'). They are passed directly to the
Subsonic createPlaylist / updatePlaylist endpoints.

NAVIDROME_PASS is never logged.
"""
import logging

from app.clients.navidrome_client import NavidromeClient, NavidromeError

logger = logging.getLogger(__name__)


class NavidromePusher:
    """Create or update Navidrome playlists via the Subsonic API."""

    def __init__(self, client: NavidromeClient):
        self._client = client

    def push_playlist(self, name: str, track_ids: list[str]) -> str | None:
        """Create or update a playlist by name. Returns playlist ID or None on failure.

        If a playlist with this name already exists, appends the tracks to it.
        If not, creates a new playlist.
        track_ids are Navidrome song IDs (lib_tracks.id when platform=navidrome).
        """
        if not track_ids:
            logger.warning("NavidromePusher.push_playlist called with empty track list")
            return None

        try:
            existing = self._find_playlist(name)
            if existing:
                playlist_id = existing["id"]
                self._client.update_playlist(playlist_id, track_ids)
                logger.info(
                    "NavidromePusher: updated playlist '%s' (%s) with %d tracks",
                    name, playlist_id, len(track_ids),
                )
                return playlist_id
            else:
                created = self._client.create_playlist(name, track_ids)
                playlist_id = created.get("id")
                logger.info(
                    "NavidromePusher: created playlist '%s' (%s) with %d tracks",
                    name, playlist_id, len(track_ids),
                )
                return playlist_id
        except NavidromeError as exc:
            logger.error("NavidromePusher: failed to push playlist '%s': %s", name, exc)
            return None

    def _find_playlist(self, name: str) -> dict | None:
        """Return the first playlist matching name, or None."""
        for pl in self._client.get_playlists():
            if pl.get("name") == name:
                return pl
        return None
