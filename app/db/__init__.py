"""
Library backend selector.

Swap the LIBRARY_BACKEND env var to change the music library source.
All backends implement the same public interface as soulsync_reader.py.

Valid values: "soulsync" (default) | "plex" | "jellyfin" | "navidrome"

Resolution order for get_library_reader():
  1. cc_settings 'library_backend' (set via Settings UI — persists across restarts)
  2. LIBRARY_BACKEND env var (bootstrap default)
"""
from app import config


def get_library_reader():
    """Return the library reader module for the configured backend.

    Checks cc_settings first so the UI can change the backend without a
    container restart. Falls back to the LIBRARY_BACKEND env var.
    """
    backend = config.LIBRARY_BACKEND  # default

    try:
        from app.db import cc_store
        saved = cc_store.get_setting("library_backend")
        if saved:
            backend = saved
    except Exception:
        pass  # cc.db not ready yet (first boot) — use env var default

    if backend == "plex":
        from app.db import plex_reader as reader
    elif backend == "jellyfin":
        from app.db import jellyfin_reader as reader
    elif backend == "navidrome":
        from app.db import navidrome_reader as reader
    else:
        from app.db import soulsync_reader as reader

    return reader
