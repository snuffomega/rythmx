"""
Library platform selector.

Swap the LIBRARY_PLATFORM env var to change the music library source.
All platforms implement the same public interface as plex_reader.py.

Valid values: "navidrome" (default) | "plex" | "jellyfin"

SoulSync is an enrichment API — it does not populate lib_* tables and
is not a valid library platform. See app/db/soulsync_reader.py.

Resolution order for get_library_reader():
  1. app_settings 'library_platform' (set via Settings UI — persists across restarts)
  2. LIBRARY_PLATFORM env var (bootstrap default)
"""
import logging

from app import config

logger = logging.getLogger(__name__)


def get_library_reader():
    """Return the library reader module for the configured platform.

    Checks app_settings first so the UI can change the platform without a
    container restart. Falls back to the LIBRARY_PLATFORM env var.
    """
    platform = config.LIBRARY_PLATFORM  # default

    try:
        from app.db import rythmx_store
        saved = rythmx_store.get_setting("library_platform")
        if saved:
            platform = saved
    except Exception:
        pass  # rythmx.db not ready yet (first boot) — use env var default

    if platform == "soulsync":
        logger.warning(
            "library_platform='soulsync' is no longer valid — SoulSync is an enrichment API, "
            "not a library platform. Falling back to 'plex'. Update your settings."
        )
        platform = "plex"

    if platform == "jellyfin":
        from app.db import jellyfin_reader as reader
    elif platform == "navidrome":
        from app.db import navidrome_reader as reader
    else:
        from app.db import plex_reader as reader

    return reader


def get_playlist_pusher():
    """Return the playlist pusher for the configured platform.

    Uses the same resolution order as get_library_reader():
      1. app_settings 'library_platform' (UI-persisted)
      2. LIBRARY_PLATFORM env var
    """
    platform = config.LIBRARY_PLATFORM

    try:
        from app.db import rythmx_store
        saved = rythmx_store.get_setting("library_platform")
        if saved:
            platform = saved
    except Exception:
        pass

    if platform == "navidrome":
        from app.clients.navidrome_client import NavidromeClient
        from app.clients.navidrome_push import NavidromePusher
        try:
            from app.db import rythmx_store as _store
            url = _store.get_setting("navidrome_url") or config.NAVIDROME_URL
            user = _store.get_setting("navidrome_user") or config.NAVIDROME_USER
            password = _store.get_setting("navidrome_pass") or config.NAVIDROME_PASS
        except Exception:
            url = config.NAVIDROME_URL
            user = config.NAVIDROME_USER
            password = config.NAVIDROME_PASS
        client = NavidromeClient(url, user, password)
        return NavidromePusher(client)

    if platform == "plex":
        from app.clients import plex_push
        return plex_push

    # Jellyfin and unknown — return a no-op stub
    class _NoopPusher:
        def push_playlist(self, name, track_ids):
            logger.warning("push_playlist: no pusher implemented for platform '%s'", platform)
            return None

        def update_playlist(self, playlist_id, track_ids):
            pass

    return _NoopPusher()
