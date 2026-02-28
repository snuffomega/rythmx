"""
main.py — Flask application factory.

Registers blueprints and handles the health check, SPA serving, and startup.
All API routes live in app/routes/.
Never log secret values.
"""
import logging
from flask import Flask, jsonify, send_from_directory
from flask_talisman import Talisman
from app import config, scheduler
from app.db import cc_store

logging.basicConfig(
    level=logging.DEBUG if config.FLASK_DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../webui", static_url_path="")

    # --- Security headers ---
    Talisman(
        app,
        force_https=False,  # handled by reverse proxy in production
        content_security_policy={
            "default-src": "'self'",
            "script-src": "'self' 'unsafe-inline'",
            "style-src": "'self' 'unsafe-inline'",
            "img-src": "'self' data: https:",
            "connect-src": "'self'",
        },
        frame_options="DENY",
        content_security_policy_nonce_in=["script-src"],
    )

    # --- Init DB ---
    cc_store.init_db()

    # --- Log config summary (redacted) ---
    config.log_config_summary()

    # --- Register blueprints ---
    from app.routes.dash import dash_bp
    from app.routes.new_music import new_music_bp
    from app.routes.acquisition import acquisition_bp
    from app.routes.playlists import playlists_bp
    from app.routes.stats import stats_bp
    from app.routes.settings import settings_bp
    from app.routes.images import images_bp
    from app.routes.personal_discovery import personal_discovery_bp

    for bp in (dash_bp, new_music_bp, acquisition_bp, playlists_bp,
               stats_bp, settings_bp, images_bp, personal_discovery_bp):
        app.register_blueprint(bp)

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    @app.route("/health")
    def health():
        try:
            from app.db.cc_store import _connect
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
    logger.info("Rythmx started on %s:%d", config.FLASK_HOST, config.FLASK_PORT)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
