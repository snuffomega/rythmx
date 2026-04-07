"""
main.py — FastAPI ASGI application factory.

Registers routers, security middleware, SPA serving, and startup lifecycle.
All API routes live in app/routes/.
Never log secret values.
"""
import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app import config
from app.runners import scheduler
from app.db import rythmx_store


# ---------------------------------------------------------------------------
# Logging + secret redaction
# ---------------------------------------------------------------------------

class _SecretRedactionFilter(logging.Filter):
    """
    Belt-and-suspenders redaction filter applied to the root logger.

    Scrubs known secret values from ALL log records before they are emitted —
    including messages from third-party libraries (urllib3, requests, spotipy)
    that may log full request URLs containing API keys as query parameters.

    Also scrubs the Rythmx app api_key query parameter by regex — the key is
    a DB value not available at module load time, so pattern-based redaction
    is used rather than value-based.
    """

    # Matches ?api_key=<value> or &api_key=<value> in any logged URL
    _QS_API_KEY_RE = re.compile(r'([\?&]api_key=)[^& "\']+')

    def __init__(self):
        super().__init__()
        self._secrets = [v for v in [
            config.LASTFM_API_KEY,
            config.PLEX_TOKEN,
            config.SPOTIFY_CLIENT_ID,
            config.SPOTIFY_CLIENT_SECRET,
            config.FANART_API_KEY,
        ] if v]

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact api_key query param (Rythmx app key passed in stream URLs)
        if isinstance(record.msg, str) and "api_key=" in record.msg:
            record.msg = self._QS_API_KEY_RE.sub(r"\1[REDACTED]", record.msg)

        for secret in self._secrets:
            if isinstance(record.msg, str) and secret in record.msg:
                record.msg = record.msg.replace(secret, "[REDACTED]")
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (v.replace(secret, "[REDACTED]") if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        (a.replace(secret, "[REDACTED]") if isinstance(a, str) else a)
                        for a in record.args
                    )
        return True


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _AccessNoiseFilter(logging.Filter):
    """
    Suppress high-churn uvicorn access-log entries that add noise.

    Drops:
      - /health
      - /assets/*
      - /static/*
      - *.svg, *.ico, *.png
      - /api/v1/artwork/* 404 (expected when stale hashes are being repaired)
      - /api/v1/forge/sync/jobs/* (frontend polls every 2s during active sync)
    """

    _REQ_RE = re.compile(r'"[A-Z]+ ([^ ]+) HTTP/[^"]+"(?: (\d{3}))?')
    _SUFFIXES = (".svg", ".ico", ".png")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        m = self._REQ_RE.search(msg)
        if not m:
            return True

        path = m.group(1).split("?", 1)[0].lower()
        status = (m.group(2) or "").strip()
        if path == "/health":
            return False
        if path.startswith("/assets/") or path.startswith("/static/"):
            return False
        if path.endswith(self._SUFFIXES):
            return False
        if path.startswith("/api/v1/artwork/") and status == "404":
            return False
        if path.startswith("/api/v1/forge/sync/jobs/"):
            return False
        return True


class _ClientAddressRedactionFilter(logging.Filter):
    """
    Redact client IP/port in uvicorn connection/access messages at non-DEBUG levels.

    Example:
      10.10.1.231:62088 - "GET /api/v1/..." 200
    becomes:
      client:*** - "GET /api/v1/..." 200
    """

    _CLIENT_PREFIX_RE = re.compile(r'^([0-9a-fA-F:.]+:\d+)(?=\s+-\s+")')

    def __init__(self):
        super().__init__()
        self._enabled = config.LOG_LEVEL.upper() != "DEBUG"

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._enabled:
            return True
        msg = record.getMessage()
        redacted = self._CLIENT_PREFIX_RE.sub("client:***", msg, count=1)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def _configure_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logging.basicConfig(level=level, format=_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addFilter(_SecretRedactionFilter())

    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # Attach filters directly to uvicorn logger objects, not their handlers.
    # This file loads before uvicorn starts, so uv_logger.handlers is empty at
    # module load time — logger-level filters are applied before handler dispatch
    # and therefore work regardless of when uvicorn adds its handlers.
    _access_log = logging.getLogger("uvicorn.access")
    _access_log.setLevel(level)
    _access_log.addFilter(_AccessNoiseFilter())
    _access_log.addFilter(_ClientAddressRedactionFilter())

    _error_log = logging.getLogger("uvicorn.error")
    _error_log.setLevel(level)
    _error_log.addFilter(_ClientAddressRedactionFilter())

    logging.getLogger("uvicorn").setLevel(level)

    # Apply formatter to any handlers already attached (usually none at this point)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)

    logging.getLogger("spotipy").setLevel(logging.WARNING)
    logging.getLogger("spotipy.client").setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security headers middleware (replaces flask-talisman)
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds HSTS, CSP, and X-Frame-Options to every response.

    force_https=False — HTTPS termination is owned by the reverse proxy.
    No unsafe-inline in script-src — the Vite bundle loads from 'self' as a
    module script; any injected inline <script> is blocked by the browser.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' ws: wss:"
        )
        return response


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Init DB ---
    rythmx_store.init_db()

    # --- Load plugins (after DB init so plugin_slots table exists) ---
    from app.plugins import load_plugins
    _slot_config = rythmx_store.get_all_plugin_slot_config()
    _plugin_settings = rythmx_store.get_all_plugin_settings()
    load_plugins(slot_config=_slot_config, plugin_settings=_plugin_settings)

    # --- Ensure local artwork storage dirs exist ---
    from app.services.artwork_store import ensure_artwork_dirs
    ensure_artwork_dirs()

    # --- Repair stale artwork hashes (background, non-blocking) ---
    # Runs in a daemon thread so startup is not blocked by filesystem stat calls.
    # Capped at 500 rows per boot — full repair is a future scheduled maintenance task.
    import threading as _threading_repair

    def _artwork_hash_repair() -> None:
        try:
            from app.services.enrichment.artwork_repair import reset_missing_content_hashes
            result = reset_missing_content_hashes(entity_types=("album", "artist"), limit=500)
            if result.get("reset", 0):
                logger.info(
                    "Artwork hash repair (background): scanned=%d reset=%d",
                    result.get("scanned", 0),
                    result.get("reset", 0),
                )
        except Exception as exc:
            logger.warning("Background artwork hash repair failed (non-fatal): %s", exc)

    _threading_repair.Thread(
        target=_artwork_hash_repair,
        daemon=True,
        name="artwork-hash-repair",
    ).start()

    # --- Ensure API key exists (auto-generate on first boot) ---
    if not rythmx_store.get_api_key():
        rythmx_store.generate_new_api_key()
        logger.info("API key generated (first boot) — retrieve it from Settings > Security")

    # --- Log config summary (redacted) ---
    config.log_config_summary()

    # --- WebSocket: bind running event loop so broadcast() works from threads ---
    from app.routes.ws import set_event_loop, _start_heartbeat
    set_event_loop(asyncio.get_running_loop())
    _start_heartbeat()

    # --- Scheduler ---
    scheduler.start()

    # --- Startup library pipeline ---
    try:
        from app.runners.scheduler import _should_library_sync as _check_lib_sync
        _startup_settings = rythmx_store.get_all_settings()
        _lib_empty = False
        try:
            from app.db.rythmx_store import _connect as _rc
            with _rc() as _c:
                _lib_empty = _c.execute("SELECT COUNT(*) FROM lib_artists").fetchone()[0] == 0
        except Exception:
            pass
        if _lib_empty or _check_lib_sync(_startup_settings):
            import threading as _threading

            def _startup_pipeline():
                # Orchestrator delegates to PipelineRunner (single control plane).
                from app.services.api_orchestrator import EnrichmentOrchestrator
                EnrichmentOrchestrator.get().run_full()

            _threading.Thread(
                target=_startup_pipeline,
                daemon=True,
                name="lib-pipeline-startup",
            ).start()
            logger.info("Library auto-pipeline triggered on startup")
    except Exception as _e:
        logger.warning("Startup library sync check failed (non-fatal): %s", _e)

    logger.info("Rythmx started on %s:%d", config.RYTHMX_HOST, config.RYTHMX_PORT)

    # --- Thread supervisor (proactive crash detection) ---
    _supervisor = asyncio.create_task(_thread_supervisor())

    yield

    _supervisor.cancel()
    try:
        await _supervisor
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Rythmx API",
    version="1.0.0",
    docs_url=None,       # disable Swagger UI in production
    redoc_url=None,      # disable ReDoc in production
    lifespan=lifespan,
)

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

# CORS — only active when CORS_ORIGINS is explicitly configured.
# Off by default (same-origin deployment). Required for Expo web dev at localhost:8081.
if config.CORS_ORIGINS:
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-Api-Key", "Content-Type", "Authorization"],
    )

# --- Register routers at /api/v1 ---
from app.routes.auth import router as auth_router
from app.routes.acquisition import router as acquisition_router
from app.routes.stats import router as stats_router
from app.routes.settings import router as settings_router
from app.routes.images import router as images_router
from app.routes.artwork import router as artwork_router
from app.routes.library.artists import router as library_artists_router
from app.routes.library.releases import router as library_releases_router
from app.routes.library.albums import router as library_albums_router
from app.routes.library.tracks import router as library_tracks_router
from app.routes.library.audit import router as library_audit_router
from app.routes.library_enrich import router as enrich_router
from app.routes.library_stream import router as library_stream_router
from app.routes.forge import router as forge_router
from app.routes.library_playlists import router as library_playlists_router
from app.routes.ws import router as ws_router

for _router in (
    auth_router, acquisition_router,
    stats_router, settings_router, images_router, artwork_router,
    library_artists_router, library_releases_router, library_albums_router, library_tracks_router,
    library_audit_router, enrich_router, library_stream_router, forge_router,
    library_playlists_router,
):
    app.include_router(_router, prefix="/api/v1")

# WebSocket is registered at root (not under /api/v1)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

# Always-on daemon threads expected to be alive for the process lifetime.
# Missing either → status: degraded.
_ALWAYS_ON_THREADS = frozenset({"maintenance-scheduler", "ws-heartbeat"})

_SUPERVISOR_INTERVAL_S = 60  # check every 60 seconds


async def _thread_supervisor() -> None:
    """
    Async lifespan task — logs an ERROR if any always-on daemon thread stops
    running. Fires every _SUPERVISOR_INTERVAL_S seconds.

    This catches silent crashes in the scheduler or WebSocket heartbeat that
    would otherwise go unnoticed until the /health endpoint is polled.
    """
    while True:
        await asyncio.sleep(_SUPERVISOR_INTERVAL_S)
        alive = {t.name for t in threading.enumerate() if t.is_alive()}
        for name in _ALWAYS_ON_THREADS:
            if name not in alive:
                logger.error(
                    "Daemon thread '%s' is no longer alive — service may be degraded",
                    name,
                )


@app.get("/health")
def health():
    alive = {t.name for t in threading.enumerate() if t.is_alive()}
    threads = {name: name in alive for name in _ALWAYS_ON_THREADS}
    thread_ok = all(threads.values())
    try:
        from app.db.rythmx_store import _connect
        with _connect() as conn:
            wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        status = "ok" if thread_ok else "degraded"
        return {"status": status, "db": "connected", "wal": wal == "wal", "threads": threads}
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Static / SPA catch-all
# ---------------------------------------------------------------------------

_WEBUI_DIR = Path(__file__).parent.parent / "webui"

if (_WEBUI_DIR / "assets").exists():
    # Mount static assets (JS, CSS, images) — these are matched before the catch-all
    app.mount("/assets", StaticFiles(directory=str(_WEBUI_DIR / "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_catch_all(full_path: str):
    """Serve index.html for all non-API, non-asset paths so React Router handles navigation."""
    index = _WEBUI_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"status": "error", "message": "Frontend not built"}, status_code=503)
