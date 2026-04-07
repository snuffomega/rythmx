"""
config.py — load all configuration from environment variables.
Never hardcode secrets. Raise clearly if required vars are missing.
"""
import os
import logging
import re
from urllib.parse import urlsplit
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

# --- Navidrome ---
# Required when LIBRARY_PLATFORM=navidrome.
# Base URL of your Navidrome server (e.g. http://10.10.1.10:4533)
NAVIDROME_URL = _optional("NAVIDROME_URL", "")
# Navidrome username and password (used for token-mode auth — password never sent in plaintext)
NAVIDROME_USER = _optional("NAVIDROME_USER", "")
NAVIDROME_PASS = _optional("NAVIDROME_PASS", "")

# --- Spotify ---
SPOTIFY_CLIENT_ID = _optional("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _optional("SPOTIFY_CLIENT_SECRET")
# Rate limit for Spotify API calls. Default 100 RPM is conservative.
# Lower if you hit 429s; Spotify's actual limit varies and changes over time.
SPOTIFY_RATE_LIMIT_RPM = int(_optional("SPOTIFY_RATE_LIMIT_RPM", "100"))

# --- Server ---
# Primary: RYTHMX_HOST / RYTHMX_PORT / RYTHMX_DEBUG
# Deprecated fallback: FLASK_HOST / FLASK_PORT / FLASK_DEBUG (still works, warns once)
def _server_var(new_key: str, old_key: str, default: str) -> str:
    """Read new env var, fall back to deprecated Flask-era name, then default."""
    val = os.environ.get(new_key, "")
    if val:
        return val
    old_val = os.environ.get(old_key, "")
    if old_val:
        logger.warning("%s env var is deprecated — rename to %s in your .env", old_key, new_key)
        return old_val
    return default

RYTHMX_HOST = _server_var("RYTHMX_HOST", "FLASK_HOST", "0.0.0.0")
RYTHMX_PORT = int(_server_var("RYTHMX_PORT", "FLASK_PORT", "8009"))
RYTHMX_DEBUG = _server_var("RYTHMX_DEBUG", "FLASK_DEBUG", "false").lower() == "true"


def _log_level() -> str:
    """
    Read log level from canonical env var, with backward-compat fallback.

    Canonical: RYTHMX_LOG_LEVEL
    Deprecated fallback: LOG_LEVEL
    """
    val = os.environ.get("RYTHMX_LOG_LEVEL", "").strip()
    if val:
        return val.upper()

    old_val = os.environ.get("LOG_LEVEL", "").strip()
    if old_val:
        logger.warning(
            "LOG_LEVEL env var is deprecated — rename to RYTHMX_LOG_LEVEL in your .env"
        )
        return old_val.upper()

    return "DEBUG" if RYTHMX_DEBUG else "INFO"


LOG_LEVEL = _log_level()

# --- Catalog primary source ---
# Which API catalog populates lib_releases for gap analysis (missing shelf).
# The OTHER source still has its IDs captured in lib_artist_catalog for enrichment.
# Valid values: "deezer" (recommended — explicit record_type, 500-item cap) | "itunes"
CATALOG_PRIMARY = _optional("CATALOG_PRIMARY", "deezer").lower()
if CATALOG_PRIMARY not in ("deezer", "itunes"):
    logger.warning("CATALOG_PRIMARY=%s is invalid, falling back to 'deezer'", CATALOG_PRIMARY)
    CATALOG_PRIMARY = "deezer"

# --- Deezer BPM ---
# Manual-only enrichment for track BPM via Deezer.
# Disabled by default due to heavy rate-limit profile (~13 API calls per album).
# See bpm_deezer.py docstring for full rate-limit math.
DEEZER_BPM_ENABLED = _optional("DEEZER_BPM_ENABLED", "false").lower() in ("true", "1", "yes")

# --- Music catalog API ---
# auto = Spotify if credentials set, otherwise Deezer, MusicBrainz as fallback
MUSIC_API_PROVIDER = _optional("MUSIC_API_PROVIDER", "auto")  # auto|deezer|spotify|musicbrainz
# Release kinds filter for discovery (used by Forge new-music pipeline and artist release lookups).
RELEASE_KINDS = _optional("RELEASE_KINDS", "album,single,ep")

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
    LIBRARY_PLATFORM = "navidrome"

# --- WebSocket ---
# Comma-separated substrings checked against the Origin header on WS upgrade.
# Empty (default) = allow any origin — appropriate for self-hosted LAN deployments.
# Set to restrict: WS_ALLOWED_ORIGINS=mysite.example.com,192.168.1.0
WS_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in _optional("WS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]

# --- CORS ---
# Comma-separated allowed origins for cross-origin HTTP requests.
# Empty (default) = CORS middleware not mounted — correct for same-origin deployments.
# Required for Expo web dev against a local server: CORS_ORIGINS=http://localhost:8081
# Never set to '*' — always list explicit origins.
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in _optional("CORS_ORIGINS", "").split(",")
    if o.strip()
]

# --- Music directory (optional) ---
# Absolute path to your music files (same mount Plex reads from).
# When set, enables local artwork lookup (folder.jpg / cover.png) and
# future file-aware features (tag enrichment, embedded artwork, codec info).
# When not set, all features depending on local files are silently skipped.
MUSIC_DIR = _optional("MUSIC_DIR") or None  # normalize "" → None

# --- Local artwork store directory ---
# Content-addressed artwork originals + thumbnail cache root.
# Subdirectories (created on startup): originals/, cache/
ARTWORK_DIR = os.path.abspath(_optional("ARTWORK_DIR", "./data/artwork/"))

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

    def _mask_url(value: str) -> str:
        if not value:
            return "(not set)"
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            if not host:
                return "***"

            if re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                parts = host.split(".")
                host_masked = ".".join(parts[:3] + ["***"])
            else:
                chunks = host.split(".")
                if len(chunks) >= 2:
                    first = chunks[0]
                    chunks[0] = (first[:2] + "***") if first else "***"
                    host_masked = ".".join(chunks)
                else:
                    host_masked = host[:2] + "***"

            netloc = host_masked
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return f"{parsed.scheme or 'http'}://{netloc}"
        except Exception:
            return "***"

    def _mask(field: str, value) -> str:
        text = "" if value is None else str(value)
        upper_field = field.upper()

        if any(token in upper_field for token in ("TOKEN", "KEY", "SECRET", "PASS")):
            return "***" if text else "(not set)"
        if "URL" in upper_field:
            return _mask_url(text)
        if "USER" in upper_field:
            if not text:
                return "(not set)"
            if len(text) <= 2:
                return "*" * len(text)
            return f"{text[:2]}***"
        return text if text else "(not set)"

    logger.info("Rythmx config loaded:")
    pairs = [
        ("RYTHMX_DB", RYTHMX_DB),
        ("LIBRARY_PLATFORM", LIBRARY_PLATFORM),
        ("PLEX_URL", PLEX_URL if LIBRARY_PLATFORM == "plex" else ""),
        ("PLEX_TOKEN", PLEX_TOKEN if LIBRARY_PLATFORM == "plex" else ""),
        ("NAVIDROME_URL", NAVIDROME_URL if LIBRARY_PLATFORM == "navidrome" else ""),
        ("NAVIDROME_USER", NAVIDROME_USER if LIBRARY_PLATFORM == "navidrome" else ""),
        ("NAVIDROME_PASS", "***" if (LIBRARY_PLATFORM == "navidrome" and NAVIDROME_PASS) else ""),
        ("SOULSYNC_DB", SOULSYNC_DB),
        ("SOULSYNC_URL", SOULSYNC_URL),
        ("LASTFM_USERNAME", LASTFM_USERNAME),
        ("LASTFM_API_KEY", LASTFM_API_KEY),
        ("SPOTIFY_CLIENT_ID", SPOTIFY_CLIENT_ID),
        ("FANART_API_KEY", FANART_API_KEY),
        ("MUSIC_DIR", MUSIC_DIR or "NOT SET (file-aware features disabled)"),
        ("ARTWORK_DIR", ARTWORK_DIR),
        ("CATALOG_PRIMARY", CATALOG_PRIMARY),
    ]

    for key, value in pairs:
        if key.startswith("NAVIDROME_") and LIBRARY_PLATFORM != "navidrome":
            continue
        if key.startswith("PLEX_") and LIBRARY_PLATFORM != "plex":
            continue
        logger.info("  %s: %s", key, _mask(key, value))
