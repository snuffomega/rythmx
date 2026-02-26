"""
config.py — load all configuration from environment variables.
Never hardcode secrets. Raise clearly if required vars are missing.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise ValueError(f"Required environment variable not set: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# --- Paths ---
SOULSYNC_DB = _optional("SOULSYNC_DB", "/data/soulsync/music_library.db")
CC_DB = _optional("CC_DB", "/data/cc/cc.db")

# --- SoulSync API ---
SOULSYNC_URL = _optional("SOULSYNC_URL", "http://soulsync:8008")

# --- Last.fm ---
LASTFM_API_KEY = _optional("LASTFM_API_KEY")
LASTFM_USERNAME = _optional("LASTFM_USERNAME")
LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# --- Plex ---
PLEX_URL = _optional("PLEX_URL")
PLEX_TOKEN = _optional("PLEX_TOKEN")
PLEX_MUSIC_SECTION = _optional("PLEX_MUSIC_SECTION", "Music")

# --- Library DB (plex_reader local cache) ---
LIBRARY_DB = _optional("LIBRARY_DB", "/data/cc/library.db")

# --- Spotify ---
SPOTIFY_CLIENT_ID = _optional("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _optional("SPOTIFY_CLIENT_SECRET")

# --- Flask ---
FLASK_HOST = _optional("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(_optional("FLASK_PORT", "8009"))
FLASK_DEBUG = _optional("FLASK_DEBUG", "false").lower() == "true"

# --- Cruise Control defaults ---
CC_ENABLED = _optional("CC_ENABLED", "false").lower() == "true"
CC_CYCLE_HOURS = int(_optional("CC_CYCLE_HOURS", "24"))
CC_MAX_PER_CYCLE = int(_optional("CC_MAX_PER_CYCLE", "10"))
CC_MIN_LISTENS = int(_optional("CC_MIN_LISTENS", "5"))
CC_LOOKBACK_DAYS = int(_optional("CC_LOOKBACK_DAYS", "90"))
CC_IGNORE_KEYWORDS = _optional("CC_IGNORE_KEYWORDS", "remix,remaster,live,karaoke,instrumental")
CC_RELEASE_KINDS = _optional("CC_RELEASE_KINDS", "album,single,ep")

# --- Music catalog API ---
# auto = Spotify if credentials set, otherwise Deezer, MusicBrainz as fallback
MUSIC_API_PROVIDER = _optional("MUSIC_API_PROVIDER", "auto")  # auto|deezer|spotify|musicbrainz

# --- Library backend ---
# Swap to pivot from SoulSync DB to a direct player API reader.
# All backends implement the same interface as soulsync_reader.py.
# Valid values: "soulsync" | "plex" | "jellyfin" | "navidrome"
LIBRARY_BACKEND = _optional("LIBRARY_BACKEND", "soulsync")


def validate_lastfm():
    if not LASTFM_API_KEY or not LASTFM_USERNAME:
        raise ValueError("LASTFM_API_KEY and LASTFM_USERNAME are required for Last.fm features")


def validate_plex():
    if not PLEX_URL or not PLEX_TOKEN:
        raise ValueError("PLEX_URL and PLEX_TOKEN are required for Plex playlist push")


def log_config_summary():
    """Log a redacted config summary on startup (never log secret values)."""
    logger.info("Rythmx config loaded:")
    logger.info("  SOULSYNC_DB: %s", SOULSYNC_DB)
    logger.info("  CC_DB: %s", CC_DB)
    logger.info("  SOULSYNC_URL: %s", SOULSYNC_URL)
    logger.info("  LASTFM_USERNAME: %s", LASTFM_USERNAME or "(not set)")
    logger.info("  LASTFM_API_KEY: %s", "set" if LASTFM_API_KEY else "NOT SET")
    logger.info("  PLEX_URL: %s", PLEX_URL or "(not set)")
    logger.info("  PLEX_TOKEN: %s", "set" if PLEX_TOKEN else "NOT SET")
    logger.info("  SPOTIFY_CLIENT_ID: %s", "set" if SPOTIFY_CLIENT_ID else "NOT SET")
    logger.info("  CC_ENABLED: %s", CC_ENABLED)
    logger.info("  LIBRARY_BACKEND: %s", LIBRARY_BACKEND)
    logger.info("  LIBRARY_DB: %s", LIBRARY_DB)
