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
RYTHMX_DB = _optional("RYTHMX_DB", "/data/rythmx/rythmx.db")

# --- SoulSync enrichment API ---
SOULSYNC_URL = _optional("SOULSYNC_URL", "http://soulsync:8008")

# --- Last.fm ---
LASTFM_API_KEY = _optional("LASTFM_API_KEY")
LASTFM_USERNAME = _optional("LASTFM_USERNAME")
LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# --- Plex ---
PLEX_URL = _optional("PLEX_URL")
PLEX_TOKEN = _optional("PLEX_TOKEN")
PLEX_MUSIC_SECTION = _optional("PLEX_MUSIC_SECTION", "Music")

# --- Spotify ---
SPOTIFY_CLIENT_ID = _optional("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _optional("SPOTIFY_CLIENT_SECRET")
# Rate limit for Spotify API calls. Default 100 RPM is conservative.
# Lower if you hit 429s; Spotify's actual limit varies and changes over time.
SPOTIFY_RATE_LIMIT_RPM = int(_optional("SPOTIFY_RATE_LIMIT_RPM", "100"))

# --- Flask ---
FLASK_HOST = _optional("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(_optional("FLASK_PORT", "8009"))
FLASK_DEBUG = _optional("FLASK_DEBUG", "false").lower() == "true"
LOG_LEVEL = _optional("LOG_LEVEL", "DEBUG" if FLASK_DEBUG else "INFO").upper()

# --- Cruise Control / app defaults ---
SCHEDULER_ENABLED = _optional("SCHEDULER_ENABLED", "false").lower() == "true"
CYCLE_HOURS = int(_optional("CYCLE_HOURS", "24"))
MAX_PER_CYCLE = int(_optional("MAX_PER_CYCLE", "10"))
MIN_LISTENS = int(_optional("MIN_LISTENS", "5"))
LOOKBACK_DAYS = int(_optional("LOOKBACK_DAYS", "90"))
IGNORE_KEYWORDS = _optional("IGNORE_KEYWORDS", "remix,remaster,live,karaoke,instrumental")
RELEASE_KINDS = _optional("RELEASE_KINDS", "album,single,ep")

# --- Music catalog API ---
# auto = Spotify if credentials set, otherwise Deezer, MusicBrainz as fallback
MUSIC_API_PROVIDER = _optional("MUSIC_API_PROVIDER", "auto")  # auto|deezer|spotify|musicbrainz

# --- Library platform ---
# Which media server platform populates the library (lib_* tables).
# Valid values: "plex" | "jellyfin" | "navidrome"
# SoulSync is an enrichment API, not a platform — do not set here.
# Backward compat: LIBRARY_BACKEND is read if LIBRARY_PLATFORM is not set.
_lp_new = os.environ.get("LIBRARY_PLATFORM", "")
_lp_old = os.environ.get("LIBRARY_BACKEND", "")
if _lp_new:
    LIBRARY_PLATFORM = _lp_new
elif _lp_old:
    logger.warning(
        "LIBRARY_BACKEND env var is deprecated — rename to LIBRARY_PLATFORM in your .env"
    )
    LIBRARY_PLATFORM = _lp_old
else:
    LIBRARY_PLATFORM = "plex"

# --- WebSocket ---
# Comma-separated substrings checked against the Origin header on WS upgrade.
# Empty (default) = allow any origin — appropriate for self-hosted LAN deployments.
# Set to restrict: WS_ALLOWED_ORIGINS=mysite.example.com,192.168.1.0
WS_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in _optional("WS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]

# --- Fanart.tv (optional) ---
# Free API key from https://fanart.tv/get-an-api-key/
# When set, artist images use real band photos from Fanart.tv.
# When not set, artist images fall back to iTunes album art.
FANART_API_KEY = _optional("FANART_API_KEY")


# Genre normalization whitelist for Last.fm tags (Stage 3 S3-4 normalization).
# Maps raw Last.fm tag strings (lowercase) to canonical genre labels stored in lib_* tables.
LASTFM_GENRE_WHITELIST: dict[str, str] = {
    "rock": "Rock",
    "classic rock": "Classic Rock",
    "indie rock": "Indie Rock",
    "hard rock": "Hard Rock",
    "alternative rock": "Alternative Rock",
    "alternative": "Alternative Rock",
    "punk": "Punk",
    "punk rock": "Punk Rock",
    "post-punk": "Post-Punk",
    "pop": "Pop",
    "indie pop": "Indie Pop",
    "synth-pop": "Synth-Pop",
    "synthpop": "Synth-Pop",
    "electropop": "Electro Pop",
    "dream pop": "Dream Pop",
    "hip-hop": "Hip-Hop",
    "hip hop": "Hip-Hop",
    "rap": "Hip-Hop",
    "trap": "Trap",
    "electronic": "Electronic",
    "electronica": "Electronic",
    "edm": "Electronic",
    "techno": "Techno",
    "house": "House",
    "deep house": "Deep House",
    "ambient": "Ambient",
    "idm": "IDM",
    "experimental": "Experimental",
    "noise": "Noise",
    "jazz": "Jazz",
    "jazz fusion": "Jazz Fusion",
    "smooth jazz": "Jazz",
    "blues": "Blues",
    "soul": "Soul",
    "rnb": "R&B",
    "r&b": "R&B",
    "rhythm and blues": "R&B",
    "funk": "Funk",
    "funk rock": "Funk Rock",
    "folk": "Folk",
    "indie folk": "Indie Folk",
    "folk rock": "Folk Rock",
    "country": "Country",
    "americana": "Americana",
    "bluegrass": "Bluegrass",
    "classical": "Classical",
    "orchestral": "Orchestral",
    "post-rock": "Post-Rock",
    "post rock": "Post-Rock",
    "shoegaze": "Shoegaze",
    "metal": "Metal",
    "heavy metal": "Heavy Metal",
    "death metal": "Death Metal",
    "black metal": "Black Metal",
    "doom metal": "Doom Metal",
    "progressive metal": "Progressive Metal",
    "prog metal": "Progressive Metal",
    "progressive rock": "Progressive Rock",
    "prog rock": "Progressive Rock",
    "psychedelic": "Psychedelic Rock",
    "psychedelic rock": "Psychedelic Rock",
    "reggae": "Reggae",
    "ska": "Ska",
    "pop punk": "Pop Punk",
    "emo": "Emo",
    "screamo": "Screamo",
    "grunge": "Grunge",
    "new wave": "New Wave",
    "darkwave": "Darkwave",
    "gothic rock": "Gothic Rock",
    "lo-fi": "Lo-Fi",
    "lo fi": "Lo-Fi",
    "singer-songwriter": "Singer-Songwriter",
    "acoustic": "Acoustic",
    "world": "World Music",
    "latin": "Latin",
    "bossa nova": "Bossa Nova",
    "disco": "Disco",
}


def validate_lastfm():
    if not LASTFM_API_KEY or not LASTFM_USERNAME:
        raise ValueError("LASTFM_API_KEY and LASTFM_USERNAME are required for Last.fm features")


def validate_plex():
    if not PLEX_URL or not PLEX_TOKEN:
        raise ValueError("PLEX_URL and PLEX_TOKEN are required for Plex playlist push")


def log_config_summary():
    """Log a redacted config summary on startup (never log secret values)."""
    logger.info("Rythmx config loaded:")
    logger.info("  RYTHMX_DB: %s", RYTHMX_DB)
    logger.info("  LIBRARY_PLATFORM: %s", LIBRARY_PLATFORM)
    if LIBRARY_PLATFORM == "plex":
        logger.info("  PLEX_URL: %s", PLEX_URL or "(not set)")
        logger.info("  PLEX_TOKEN: %s", "set" if PLEX_TOKEN else "NOT SET")
    logger.info("  SOULSYNC_DB: %s", SOULSYNC_DB)
    logger.info("  SOULSYNC_URL: %s", SOULSYNC_URL)
    logger.info("  LASTFM_USERNAME: %s", LASTFM_USERNAME or "(not set)")
    logger.info("  LASTFM_API_KEY: %s", "set" if LASTFM_API_KEY else "NOT SET")
    logger.info("  SPOTIFY_CLIENT_ID: %s", "set" if SPOTIFY_CLIENT_ID else "NOT SET")
    logger.info("  FANART_API_KEY: %s", "set" if FANART_API_KEY else "NOT SET (artist images fall back to iTunes)")
    logger.info("  SCHEDULER_ENABLED: %s", SCHEDULER_ENABLED)
