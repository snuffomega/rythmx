"""
main.py — Flask application factory.

Registers blueprints and handles the health check, SPA serving, and startup.
All API routes live in app/routes/.
Never log secret values.
"""
import hmac
import logging
from flask import Flask, jsonify, request, send_from_directory
from flask_talisman import Talisman
from app import config
from app.runners import scheduler
from app.db import rythmx_store
from app.routes.ws import sock


class _SecretRedactionFilter(logging.Filter):
    """
    Belt-and-suspenders redaction filter applied to the root logger.

    Scrubs known secret values from ALL log records before they are emitted —
    including messages from third-party libraries (urllib3, requests, spotipy)
    that may log full request URLs containing API keys as query parameters.

    Matches against record.msg and every item in record.args so the secret
    is removed whether it appears in the format string or as an argument.
    """

    def __init__(self):
        super().__init__()
        # Collect all non-empty secret values at startup
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
# Attach redaction filter to root logger — catches all child loggers including urllib3
logging.getLogger().addFilter(_SecretRedactionFilter())
# Silence spotipy's internal request logger — it logs Bearer tokens in Authorization headers
logging.getLogger("spotipy").setLevel(logging.WARNING)
logging.getLogger("spotipy.client").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../webui", static_url_path="")

    # --- WebSocket ---
    # Pass allowed origins from config so ws_handler can check them at runtime.
    app.config["WS_ALLOWED_ORIGINS"] = config.WS_ALLOWED_ORIGINS
    sock.init_app(app)

    # --- Security headers ---
    # 'unsafe-inline' is intentionally absent from script-src: the Vite bundle
    # loads from 'self' as a module script — no inline scripts are ever needed.
    # Removing it means any injected inline <script> is blocked by the browser.
    Talisman(
        app,
        force_https=False,              # HTTPS termination owned by reverse proxy
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,        # 1 year
        strict_transport_security_include_subdomains=True,
        strict_transport_security_preload=True,            # enables HSTS preload list
        content_security_policy={
            "default-src": "'self'",
            "script-src": "'self'",    # no unsafe-inline — Vite bundle loads via 'self'
            "style-src": "'self' 'unsafe-inline'",  # Tailwind runtime needs this
            "img-src": "'self' data: https:",
            "connect-src": "'self' ws: wss:",
        },
        frame_options="DENY",
    )

    # --- Init DB ---
    rythmx_store.init_db()

    # --- Ensure API key exists (auto-generate on first boot) ---
    if not rythmx_store.get_api_key():
        rythmx_store.generate_new_api_key()
        logger.info("API key generated (first boot) — retrieve it from Settings > Security")

    # --- API key enforcement ---
    # All /api/v1/ routes require X-Api-Key. Exempt: /auth/bootstrap (web UI seeding).
    @app.before_request
    def _require_api_key():
        if not request.path.startswith('/api/v1/'):
            return  # SPA routes, /health, and /ws pass through — origin check in ws_handler
        if request.path == '/api/v1/auth/bootstrap':
            return  # public seeding endpoint
        provided = request.headers.get('X-Api-Key', '')
        stored = rythmx_store.get_api_key() or ''
        if not hmac.compare_digest(provided, stored):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    # --- Log config summary (redacted) ---
    config.log_config_summary()

    # --- Register blueprints ---
    from app.routes.auth import auth_bp
    from app.routes.dash import dash_bp
    from app.routes.new_music import new_music_bp
    from app.routes.acquisition import acquisition_bp
    from app.routes.playlists import playlists_bp
    from app.routes.stats import stats_bp
    from app.routes.settings import settings_bp
    from app.routes.images import images_bp
    from app.routes.personal_discovery import personal_discovery_bp
    from app.routes.library_browse import library_browse_bp

    for bp in (auth_bp, dash_bp, new_music_bp, acquisition_bp, playlists_bp,
               stats_bp, settings_bp, images_bp, personal_discovery_bp,
               library_browse_bp):
        app.register_blueprint(bp, url_prefix="/api/v1")

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    @app.route("/health")
    def health():
        try:
            from app.db.rythmx_store import _connect
            with _connect() as conn:
                wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            return jsonify({"status": "ok", "db": "connected", "wal": wal == "wal"})
        except Exception as exc:
            logger.error("Health check failed: %s", exc)
            return jsonify({"status": "error", "detail": str(exc)}), 500

    # -------------------------------------------------------------------------
    # Static / SPA catch-all
    # -------------------------------------------------------------------------

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/<path:path>")
    def spa_catch_all(path):
        """Serve index.html for all non-API routes so React Router handles navigation."""
        return send_from_directory(app.static_folder, "index.html")

    # -------------------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------------------

    scheduler.start()

    # Trigger library pipeline on startup if:
    #   (a) library has never been synced / is empty, OR
    #   (b) the configured sync interval has already elapsed (avoids waiting up to 1h for
    #       the scheduler's first tick when the app restarts after a long pause).
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
            _threading.Thread(
                target=_lib_svc.run_auto_pipeline,
                daemon=True,
                name="lib-pipeline-startup",
            ).start()
            logger.info("Library auto-pipeline triggered on startup")
    except Exception as _e:
        logger.warning("Startup library sync check failed (non-fatal): %s", _e)

    logger.info("Rythmx started on %s:%d", config.FLASK_HOST, config.FLASK_PORT)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
