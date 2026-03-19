"""
main.py — FastAPI ASGI application factory.

Registers routers, security middleware, SPA serving, and startup lifecycle.
All API routes live in app/routes/.
Never log secret values.
"""
import asyncio
import logging
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
    """

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


logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger().addFilter(_SecretRedactionFilter())
logging.getLogger("spotipy").setLevel(logging.WARNING)
logging.getLogger("spotipy.client").setLevel(logging.WARNING)
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
            from app.services import library_service as _lib_svc

            def _startup_pipeline():
                _lib_svc.run_auto_pipeline()
                import time as _time
                _time.sleep(30)
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

    logger.info("Rythmx started on %s:%d", config.FLASK_HOST, config.FLASK_PORT)

    yield
    # shutdown — no cleanup needed currently


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

# --- Register routers at /api/v1 ---
from app.routes.auth import router as auth_router
from app.routes.dash import router as dash_router
from app.routes.new_music import router as new_music_router
from app.routes.acquisition import router as acquisition_router
from app.routes.playlists import router as playlists_router
from app.routes.stats import router as stats_router
from app.routes.settings import router as settings_router
from app.routes.images import router as images_router
from app.routes.personal_discovery import router as personal_discovery_router
from app.routes.library_browse import router as library_browse_router
from app.routes.library_enrich import router as enrich_router
from app.routes.ws import router as ws_router

for _router in (
    auth_router, dash_router, new_music_router, acquisition_router, playlists_router,
    stats_router, settings_router, images_router, personal_discovery_router,
    library_browse_router, enrich_router,
):
    app.include_router(_router, prefix="/api/v1")

# WebSocket is registered at root (not under /api/v1)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    try:
        from app.db.rythmx_store import _connect
        with _connect() as conn:
            wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return {"status": "ok", "db": "connected", "wal": wal == "wal"}
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Static / SPA catch-all
# ---------------------------------------------------------------------------

_WEBUI_DIR = Path(__file__).parent.parent / "webui"

if _WEBUI_DIR.exists():
    # Mount static assets (JS, CSS, images) — these are matched before the catch-all
    app.mount("/assets", StaticFiles(directory=str(_WEBUI_DIR / "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_catch_all(full_path: str):
    """Serve index.html for all non-API, non-asset paths so React Router handles navigation."""
    index = _WEBUI_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"status": "error", "message": "Frontend not built"}, status_code=503)
