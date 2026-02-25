"""
main.py — Flask application factory and route registration.

All routes return JSON with a 'status' field.
Never log secret values.
"""
import logging
import threading
from flask import Flask, jsonify, request, send_from_directory
from app import config, scheduler
from app.db import cc_store
from app import last_fm_client, plex_push, soulsync_api

# Configure logging before anything else
logging.basicConfig(
    level=logging.DEBUG if config.FLASK_DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../webui", static_url_path="")

    # --- Init DB ---
    cc_store.init_db()

    # --- Log config summary (redacted) ---
    config.log_config_summary()

    # -------------------------------------------------------------------------
    # Static / SPA
    # -------------------------------------------------------------------------

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    @app.route("/api/discovery/candidates")
    def discovery_candidates():
        from app.db import get_library_reader
        from app import engine
        sr = get_library_reader()

        candidates = sr.get_discovery_pool(limit=200)
        similar_map = sr.get_similar_artists_map()
        top_artists = last_fm_client.get_top_artists()
        loved = last_fm_client.get_loved_artist_names()

        candidates = engine.apply_owned_check(candidates, sr)
        scored = engine.score_candidates(candidates, similar_map, top_artists, loved)
        return jsonify({"status": "ok", "candidates": scored})

    @app.route("/api/discovery/playlist", methods=["GET"])
    def discovery_playlist():
        tracks = cc_store.get_playlist()
        return jsonify({"status": "ok", "playlist": tracks})

    @app.route("/api/discovery/playlist", methods=["POST"])
    def discovery_playlist_add():
        data = request.get_json(silent=True) or {}
        if not data.get("track_id") and not data.get("spotify_track_id"):
            return jsonify({"status": "error", "message": "track_id or spotify_track_id required"}), 400
        cc_store.add_to_playlist(data)
        return jsonify({"status": "ok"})

    @app.route("/api/discovery/playlist/<path:track_id>", methods=["DELETE"])
    def discovery_playlist_remove(track_id):
        cc_store.remove_from_playlist(track_id)
        return jsonify({"status": "ok"})

    @app.route("/api/discovery/download", methods=["POST"])
    def discovery_download():
        data = request.get_json(silent=True) or {}
        track = {
            "track_name": data.get("track_name"),
            "artist_name": data.get("artist_name"),
            "album_name": data.get("album_name"),
            "spotify_track_id": data.get("spotify_track_id"),
        }
        if not track["track_name"] or not track["artist_name"]:
            return jsonify({"status": "error", "message": "track_name and artist_name required"}), 400
        result = soulsync_api.queue_download(track)
        return jsonify(result)

    @app.route("/api/discovery/publish", methods=["POST"])
    def discovery_publish():
        tracks = cc_store.get_playlist()
        rating_keys = [t["track_id"] for t in tracks if t.get("track_id")]
        if not rating_keys:
            return jsonify({"status": "error", "message": "No owned tracks in playlist to push"}), 400
        # Ensure "For You" has a playlists metadata row so it surfaces correctly in the tab
        cc_store.create_playlist_meta("For You", source="cc", mode="library_only")
        playlist_id = plex_push.create_or_update_playlist("For You", rating_keys)
        if playlist_id:
            cc_store.update_playlist_plex_id("For You", playlist_id)
            return jsonify({"status": "ok", "plex_playlist_id": playlist_id})
        return jsonify({"status": "error", "message": "Plex push failed — check logs"}), 500

    @app.route("/api/discovery/export", methods=["POST"])
    def discovery_export():
        tracks = cc_store.get_playlist()
        if not tracks:
            return jsonify({"status": "error", "message": "Playlist is empty"}), 400
        lines = ["#EXTM3U"]
        for t in tracks:
            lines.append(f"#EXTINF:-1,{t.get('artist_name', '')} - {t.get('track_name', '')}")
            if t.get("spotify_track_id"):
                lines.append(f"# spotify:{t['spotify_track_id']}")
        return jsonify({"status": "ok", "content": "\n".join(lines), "filename": "for-you.m3u"})

    # -------------------------------------------------------------------------
    # Cruise Control
    # -------------------------------------------------------------------------

    @app.route("/api/cruise-control/status")
    def cc_status():
        return jsonify({"status": "ok", **scheduler.get_status()})

    @app.route("/api/cruise-control/config", methods=["GET"])
    def cc_config_get():
        settings = cc_store.get_all_settings()
        return jsonify({"status": "ok", "config": settings})

    @app.route("/api/cruise-control/config", methods=["POST"])
    def cc_config_save():
        data = request.get_json(silent=True) or {}
        allowed_keys = {
            "cc_enabled", "cc_max_per_cycle", "cc_cycle_hours",
            "cc_min_listens", "cc_period", "cc_lookback_days",
            "cc_auto_push_playlist", "cc_run_mode", "cc_playlist_prefix",
            "release_cache_refresh_weekday", "release_cache_refresh_hour",
        }
        for key, value in data.items():
            if key in allowed_keys:
                cc_store.set_setting(key, str(value))
        return jsonify({"status": "ok"})

    @app.route("/api/cruise-control/run-now", methods=["POST"])
    def cc_run_now():
        if scheduler.get_status()["is_running"]:
            return jsonify({"status": "error", "message": "A cycle is already running"}), 409
        data = request.get_json(silent=True) or {}
        run_mode = data.get("run_mode", "cruise")
        if run_mode not in ("dry", "playlist", "cruise"):
            run_mode = "cruise"
        force_refresh = bool(data.get("force_refresh", False))
        # Run in background so the request returns immediately; client polls /status
        t = threading.Thread(
            target=scheduler.run_cycle,
            args=(run_mode,),
            kwargs={"force_refresh": force_refresh},
            daemon=True,
            name="cc-manual-run",
        )
        t.start()
        return jsonify({"status": "ok", "message": "cycle_started", "run_mode": run_mode})

    @app.route("/api/release-cache/clear", methods=["POST"])
    def release_cache_clear():
        cc_store.clear_release_cache()
        return jsonify({"status": "ok", "message": "release cache cleared"})

    # -------------------------------------------------------------------------
    # Acquisition queue
    # -------------------------------------------------------------------------

    @app.route("/api/acquisition/queue", methods=["GET"])
    def acquisition_queue_get():
        status = request.args.get("status")
        playlist = request.args.get("playlist")
        items = cc_store.get_queue(status=status, playlist_name=playlist)
        return jsonify({"status": "ok", "items": items})

    @app.route("/api/acquisition/queue", methods=["POST"])
    def acquisition_queue_add():
        data = request.get_json() or {}
        artist = data.get("artist_name", "").strip()
        album = data.get("album_title", "").strip()
        if not artist or not album:
            return jsonify({"status": "error", "message": "artist_name and album_title required"}), 400
        queue_id = cc_store.add_to_queue(
            artist_name=artist, album_title=album,
            release_date=data.get("release_date"),
            kind=data.get("kind"),
            source=data.get("source"),
            requested_by="manual",
        )
        return jsonify({"status": "ok", "queue_id": queue_id})

    @app.route("/api/acquisition/stats", methods=["GET"])
    def acquisition_stats():
        stats = cc_store.get_queue_stats()
        return jsonify({"status": "ok", **stats})

    @app.route("/api/acquisition/check-now", methods=["POST"])
    def acquisition_check_now():
        """Trigger the acquisition worker immediately (re-check submitted items, timeout stale)."""
        try:
            from app import acquisition
            acquisition.check_queue()
            stats = cc_store.get_queue_stats()
            return jsonify({"status": "ok", "message": "Acquisition worker run complete", **stats})
        except Exception as e:
            logger.warning("acquisition check-now failed: %s", e)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/cruise-control/history")
    def cc_history():
        limit = min(int(request.args.get("limit", 100)), 500)
        history = cc_store.get_history(limit=limit)
        return jsonify({"status": "ok", "history": history})

    # -------------------------------------------------------------------------
    # Playlists
    # -------------------------------------------------------------------------

    def _build_taste_playlist_tracks(playlist_name: str) -> list[dict]:
        """Helper: build + save a taste-based playlist. Returns the saved tracks."""
        from app import engine, last_fm_client
        from app.db import get_library_reader
        sr = get_library_reader()

        # Respect per-playlist max_tracks and max_per_artist settings
        meta = cc_store.get_playlist_meta(playlist_name) or {}
        max_tracks = int(meta.get("max_tracks") or 50)
        max_per_artist = int(meta.get("max_per_artist") or 2)

        top_artists = last_fm_client.get_top_artists(period="6month", limit=200)
        loved = last_fm_client.get_loved_artist_names()

        logger.info(
            "Taste build '%s': %d top artists, %d loved, max_tracks=%d",
            playlist_name, len(top_artists), len(loved), max_tracks,
        )

        # Fetch library tracks for each top artist via soulsync_artist_id identity link
        artist_tracks = {}
        resolved_count = 0
        for artist_name in top_artists:
            cached = cc_store.get_cached_artist(artist_name) or {}
            ss_id = cached.get("soulsync_artist_id") or sr.get_soulsync_artist_id(artist_name)
            if ss_id:
                tracks = sr.get_all_tracks_for_artist(ss_id)
                if tracks:
                    artist_tracks[artist_name] = tracks
                    resolved_count += 1
                    logger.info("  ✓ %s → %d library tracks", artist_name, len(tracks))
                else:
                    logger.info("  ~ %s — found in SoulSync but 0 tracks returned", artist_name)
            else:
                logger.info("  ✗ %s — not found in SoulSync library", artist_name)

        logger.info(
            "Taste build: %d/%d artists resolved to library tracks",
            resolved_count, len(top_artists),
        )

        scored = engine.build_taste_playlist(
            top_artists, loved, artist_tracks,
            limit=max_tracks, max_per_artist=max_per_artist,
        )

        # Reshape to cc_store.save_playlist() expected format
        to_save = [
            {
                "plex_rating_key": t["plex_rating_key"],
                "spotify_track_id": t.get("spotify_track_id"),
                "track_name": t["track_name"],
                "artist_name": t["artist_name"],
                "album_name": t["album_name"],
                "album_cover_url": t.get("album_cover_url", ""),
                "score": t["score"],
            }
            for t in scored
        ]
        cc_store.save_playlist(to_save, playlist_name=playlist_name)
        cc_store.mark_playlist_synced(playlist_name)
        return to_save

    @app.route("/api/playlists", methods=["GET"])
    def playlists_list():
        playlists = cc_store.list_playlists()
        return jsonify({"status": "ok", "playlists": playlists})

    @app.route("/api/playlists", methods=["POST"])
    def playlists_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name is required"}), 400
        # Duplicate check
        existing = cc_store.get_playlist_meta(name)
        if existing:
            return jsonify({"status": "error", "message": f"Playlist '{name}' already exists"}), 409

        source = data.get("source", "manual")
        source_url = data.get("source_url") or None
        auto_sync = bool(data.get("auto_sync", False))
        mode = data.get("mode", "library_only")
        max_tracks = int(data.get("max_tracks") or 50)

        if source not in ("taste", "spotify", "lastfm", "deezer", "manual"):
            return jsonify({"status": "error",
                            "message": "source must be taste, spotify, lastfm, deezer, or manual"}), 400

        cc_store.create_playlist_meta(name, source=source, source_url=source_url,
                                      auto_sync=auto_sync, mode=mode, max_tracks=max_tracks)

        track_count = 0
        owned_count = 0

        if source == "taste":
            try:
                tracks = _build_taste_playlist_tracks(name)
                track_count = len(tracks)
                owned_count = sum(1 for t in tracks if t.get("plex_rating_key"))
            except Exception as e:
                logger.error("Taste playlist build failed for '%s': %s", name, e)
                return jsonify({"status": "error", "message": f"Build failed: {e}"}), 500

        elif source in ("spotify", "lastfm", "deezer"):
            if not source_url:
                return jsonify({"status": "error",
                                "message": f"source_url required for {source} import"}), 400
            from app import playlist_importer
            if source == "spotify":
                result = playlist_importer.import_from_spotify(source_url)
            elif source == "lastfm":
                result = playlist_importer.import_from_lastfm(source_url)
            else:
                result = playlist_importer.import_from_deezer(source_url)
            if result["status"] != "ok":
                return jsonify(result), 400
            to_save = [
                {
                    "plex_rating_key": t["plex_rating_key"],
                    "spotify_track_id": t.get("spotify_track_id", ""),
                    "track_name": t["track_name"],
                    "artist_name": t["artist_name"],
                    "album_name": t["album_name"],
                    "album_cover_url": "",
                    "score": None,
                }
                for t in result["tracks"]
            ]
            cc_store.save_playlist(to_save, playlist_name=name)
            cc_store.mark_playlist_synced(name)
            track_count = result["track_count"]
            owned_count = result["owned_count"]

        return jsonify({"status": "ok", "name": name,
                        "track_count": track_count, "owned_count": owned_count})

    @app.route("/api/playlists/<path:name>", methods=["DELETE"])
    def playlists_delete(name):
        cc_store.delete_playlist(name)
        return jsonify({"status": "ok"})

    @app.route("/api/playlists/<path:name>/tracks", methods=["GET"])
    def playlists_tracks(name):
        tracks = cc_store.get_playlist(playlist_name=name)
        return jsonify({"status": "ok", "tracks": tracks})

    @app.route("/api/playlists/<path:name>/build", methods=["POST"])
    def playlists_build(name):
        meta = cc_store.get_playlist_meta(name)
        if not meta:
            # Auto-create metadata row for taste playlists invoked by name
            cc_store.create_playlist_meta(name, source="taste")
        try:
            tracks = _build_taste_playlist_tracks(name)
            return jsonify({"status": "ok", "track_count": len(tracks),
                            "owned_count": sum(1 for t in tracks if t.get("plex_rating_key"))})
        except Exception as e:
            logger.error("Playlist build failed for '%s': %s", name, e)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/playlists/<path:name>/import", methods=["POST"])
    def playlists_import(name):
        meta = cc_store.get_playlist_meta(name) or {}
        source = meta.get("source", "spotify")
        source_url = meta.get("source_url")
        if not source_url:
            req_data = request.get_json(silent=True) or {}
            source_url = req_data.get("source_url")
        if not source_url:
            return jsonify({"status": "error", "message": "No source_url for this playlist"}), 400
        from app import playlist_importer
        if source == "lastfm":
            result = playlist_importer.import_from_lastfm(source_url)
        elif source == "deezer":
            result = playlist_importer.import_from_deezer(source_url)
        else:
            result = playlist_importer.import_from_spotify(source_url)
        if result["status"] != "ok":
            return jsonify(result), 400
        to_save = [
            {
                "plex_rating_key": t["plex_rating_key"],
                "spotify_track_id": t.get("spotify_track_id", ""),
                "track_name": t["track_name"],
                "artist_name": t["artist_name"],
                "album_name": t["album_name"],
                "album_cover_url": "",
                "score": None,
            }
            for t in result["tracks"]
        ]
        cc_store.save_playlist(to_save, playlist_name=name)
        cc_store.mark_playlist_synced(name)
        return jsonify({"status": "ok", "track_count": result["track_count"],
                        "owned_count": result["owned_count"]})

    @app.route("/api/playlists/<path:name>/publish", methods=["POST"])
    def playlists_publish(name):
        tracks = cc_store.get_playlist(playlist_name=name)
        # Only push owned tracks (is_owned=1, track_id present); unowned albums have no plex_rating_key
        rating_keys = [t["track_id"] for t in tracks if t.get("track_id") and t.get("is_owned", 1)]
        if not rating_keys:
            return jsonify({"status": "error", "message": "No owned tracks in playlist to push"}), 400
        playlist_id = plex_push.create_or_update_playlist(name, rating_keys)
        if playlist_id:
            cc_store.update_playlist_plex_id(name, playlist_id)
            return jsonify({"status": "ok", "plex_playlist_id": playlist_id})
        return jsonify({"status": "error", "message": "Plex push failed — check logs"}), 500

    @app.route("/api/playlists/<path:name>/export", methods=["POST"])
    def playlists_export(name):
        tracks = cc_store.get_playlist(playlist_name=name)
        if not tracks:
            return jsonify({"status": "error", "message": "Playlist is empty"}), 400
        lines = ["#EXTM3U"]
        for t in tracks:
            lines.append(f"#EXTINF:-1,{t.get('artist_name', '')} - {t.get('track_name', '')}")
            if t.get("spotify_track_id"):
                lines.append(f"# spotify:{t['spotify_track_id']}")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        return jsonify({"status": "ok", "content": "\n".join(lines),
                        "filename": f"{safe_name}.m3u"})

    @app.route("/api/playlists/<path:name>/settings", methods=["POST"])
    def playlists_settings(name):
        """Update auto_sync / mode for a playlist."""
        data = request.get_json(silent=True) or {}
        auto_sync = data.get("auto_sync")
        mode = data.get("mode")
        cc_store.update_playlist_meta(
            name,
            auto_sync=bool(auto_sync) if auto_sync is not None else None,
            mode=mode,
        )
        return jsonify({"status": "ok"})

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    @app.route("/api/stats/top-artists")
    def stats_top_artists():
        period = request.args.get("period", "6month")
        limit = min(int(request.args.get("limit", 50)), 200)
        artists = last_fm_client.get_top_artists(period=period, limit=limit)
        ranked = [{"artist": k, "playcount": v} for k, v in
                  sorted(artists.items(), key=lambda x: x[1], reverse=True)]
        return jsonify({"status": "ok", "artists": ranked, "period": period})

    @app.route("/api/stats/top-tracks")
    def stats_top_tracks():
        period = request.args.get("period", "6month")
        tracks = last_fm_client.get_top_tracks(period=period)
        return jsonify({"status": "ok", "tracks": tracks, "period": period})

    @app.route("/api/stats/summary")
    def stats_summary():
        summary = cc_store.get_history_summary()
        return jsonify({"status": "ok", "summary": summary})

    # -------------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------------

    @app.route("/api/settings", methods=["GET"])
    def settings_get():
        from app.db import get_library_reader
        lr = get_library_reader()
        # Return non-secret settings only — never return key values
        return jsonify({
            "status": "ok",
            "lastfm_username": config.LASTFM_USERNAME,
            "lastfm_configured": bool(config.LASTFM_API_KEY and config.LASTFM_USERNAME),
            "plex_url": config.PLEX_URL,
            "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
            "soulsync_url": config.SOULSYNC_URL,
            "soulsync_db": config.SOULSYNC_DB,
            "soulsync_db_accessible": lr.is_db_accessible(),
        })

    @app.route("/api/settings/test-lastfm", methods=["POST"])
    def settings_test_lastfm():
        return jsonify(last_fm_client.test_connection())

    @app.route("/api/settings/test-plex", methods=["POST"])
    def settings_test_plex():
        return jsonify(plex_push.test_connection())

    @app.route("/api/settings/test-soulsync", methods=["POST"])
    def settings_test_soulsync():
        from app.db import get_library_reader
        lr = get_library_reader()
        db_available = lr.is_db_accessible()
        api_status   = soulsync_api.test_connection()
        ok = db_available or api_status["status"] == "ok"
        return jsonify({
            "status":     "ok" if ok else "error",
            "db_available": db_available,
            "api_status": api_status,
        })

    @app.route("/api/settings/test-spotify", methods=["POST"])
    def settings_test_spotify():
        if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
            return jsonify({"status": "error", "message": "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET not set"})
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=config.SPOTIFY_CLIENT_ID,
                client_secret=config.SPOTIFY_CLIENT_SECRET,
            ))
            sp.search(q="test", type="artist", limit=1)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    @app.route("/api/settings/clear-history", methods=["POST"])
    def settings_clear_history():
        cc_store.clear_history()
        return jsonify({"status": "ok"})

    @app.route("/api/settings/reset-db", methods=["POST"])
    def settings_reset_db():
        cc_store.reset_db()
        return jsonify({"status": "ok"})

    # -------------------------------------------------------------------------
    # Stats — loved artists count
    # -------------------------------------------------------------------------

    @app.route("/api/stats/loved-artists")
    def stats_loved_artists():
        loved = last_fm_client.get_loved_artist_names()
        return jsonify({"status": "ok", "count": len(loved)})

    # -------------------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------------------

    scheduler.start()
    logger.info("Rythmx started on %s:%d", config.FLASK_HOST, config.FLASK_PORT)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
