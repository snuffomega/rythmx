# app/core/config.py
import os
from pathlib import Path
from dotenv import load_dotenv


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _detect_project_root() -> Path:
    """
    Assume this file lives at: <root>/app/core/config.py
    So project root is 2 levels up from core/: <root>
    """
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = Path(os.getenv("RYTHMX_ROOT", str(_detect_project_root()))).expanduser().resolve()

# -----------------------------------------------------------------------------
# Env file discovery (portable)
# -----------------------------------------------------------------------------
# Priority:
# 1) RYTHMX_ENV (explicit file path)
# 2) <project_root>/credentials.env
# 3) <project_root>/.env
ENV_FILE = os.getenv("RYTHMX_ENV")
if ENV_FILE:
    env_path = Path(ENV_FILE).expanduser()
else:
    candidate_credentials = PROJECT_ROOT / "credentials.env"
    candidate_dotenv = PROJECT_ROOT / ".env"
    env_path = candidate_credentials if candidate_credentials.exists() else candidate_dotenv

# Load env if present
if env_path and Path(env_path).exists():
    load_dotenv(Path(env_path))
    LOADED_ENV_FILE = str(Path(env_path))
else:
    LOADED_ENV_FILE = ""

# Optional: also load a local ".env" in current working directory (allows ad-hoc overrides)
load_dotenv()


def _get_path(env_key: str, default_rel: str) -> Path:
    """
    If env var is set, use it.
    Otherwise default to <project_root>/<default_rel>.
    """
    val = os.getenv(env_key)
    if val:
        return Path(val).expanduser()
    return (PROJECT_ROOT / default_rel).expanduser()


CONFIG = {
    # meta
    "env_file": LOADED_ENV_FILE,
    "project_root": str(PROJECT_ROOT),

    # project paths (defaults relative to project root)
    "logs_dir": str(_get_path("LOGS_DIR", "logs")),
    "state_dir": str(_get_path("STATE_DIR", "state")),
    "output_dir": str(_get_path("OUTPUT_DIR", "output")),
    "migrations_dir": str(_get_path("MIGRATIONS_DIR", "migrations")),

    # tracking
    "tracking_backend": os.getenv("TRACKING_BACKEND", "dual"),
    "dry_run": _truthy(os.getenv("RYTHMX_DRY_RUN"), default=False),

    # postgres
    "pg_host": os.getenv("PGHOST", "127.0.0.1"),
    "pg_port": int(os.getenv("PGPORT", "5432")),
    "pg_db": os.getenv("PGDATABASE", "rythmx"),
    "pg_user": os.getenv("PGUSER", "rythmx"),
    "pg_password": os.getenv("PGPASSWORD", ""),
    "pg_sslmode": os.getenv("PGSSLMODE", "prefer"),

    # spotify
    "spotipy_client_id": os.getenv("SPOTIPY_CLIENT_ID", ""),
    "spotipy_client_secret": os.getenv("SPOTIPY_CLIENT_SECRET", ""),
    "spotify_cache": os.getenv("SPOTIFY_CACHE", str(_get_path("SPOTIFY_CACHE", "state/spotify_cache.json"))),

    # lastfm
    "lastfm_api_key": os.getenv("LASTFM_API_KEY", ""),
    "lastfm_api_secret": os.getenv("LASTFM_API_SECRET", ""),
    "lastfm_username": os.getenv("LASTFM_USERNAME", ""),

    # plex
    "plex_url": os.getenv("PLEX_URL", ""),
    "plex_token": os.getenv("PLEX_TOKEN", ""),
    "plex_music_library_name": os.getenv("PLEX_MUSIC_LIBRARY_NAME", "Music"),

    # discord
    "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL", ""),

    # optional tools (not always used)
    "download_dir": os.getenv("DOWNLOAD_DIR", ""),
    "streamrip_config": os.getenv("STREAMRIP_CONFIG", ""),
    "streamrip_home_dir": os.getenv("STREAMRIP_HOME_DIR", ""),
    "beets_import_command": os.getenv("BEETS_IMPORT_COMMAND", ""),
}

# Ensure core dirs exist (portable)
for k in ("logs_dir", "state_dir", "output_dir", "migrations_dir"):
    try:
        Path(CONFIG[k]).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
