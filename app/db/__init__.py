"""
Library backend selector.

Swap the LIBRARY_BACKEND env var to change the music library source.
All backends implement the same public interface as soulsync_reader.py.

Valid values: "soulsync" (default) | "plex" | "jellyfin" | "navidrome"
"""
from app import config


def get_library_reader():
    """Return the library reader module for the configured backend."""
    backend = config.LIBRARY_BACKEND
    if backend == "plex":
        from app.db import plex_reader as reader
    elif backend == "jellyfin":
        from app.db import jellyfin_reader as reader
    elif backend == "navidrome":
        from app.db import navidrome_reader as reader
    else:
        from app.db import soulsync_reader as reader
    return reader
