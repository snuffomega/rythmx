"""
main.py — Flask application factory and route registration.

All routes return JSON with a 'status' field.
Never log secret values.
"""
import logging
import threading
from datetime import datetime
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
        raw = scheduler.get_status()
        is_running = raw.get("is_running", False)
        last_result = raw.get("last_result") or {}
        if is_running:
            state = "running"
        elif last_result:
            state = "error" if last_result.get("status") == "error" else "completed"
        else:
            state = "idle"
        summary = None
        if last_result:
            summary = {
                "artists_checked": last_result.get("artists_qualified", 0),
                "new_releases": last_result.get("releases_found", 0),
                "owned": last_result.get("releases_owned", 0),
                "queued": last_result.get("queued", 0),
            }
        return jsonify({
            "status": "ok",
            "state": state,
            "last_run": raw.get("last_run"),
            "summary": summary,
            "error": last_result.get("error"),
        })

    @app.route("/api/cruise-control/config", methods=["GET"])
    def cc_config_get():
        raw = cc_store.get_all_settings()
        # Coerce stored strings to correct types for the React UI
        bool_keys = {"cc_enabled", "cc_auto_push_playlist", "cc_dry_run"}
        int_keys = {
            "cc_min_listens", "cc_lookback_days", "cc_max_per_cycle", "cc_cycle_hours",
            "cc_schedule_weekday", "cc_schedule_hour",
            "release_cache_refresh_weekday", "release_cache_refresh_hour",
        }
        config_keys = bool_keys | int_keys | {
            "cc_run_mode", "cc_playlist_prefix", "cc_period",
            "nr_ignore_keywords", "nr_ignore_artists",
        }
        coerced = {}
        for k, v in raw.items():
            if k not in config_keys or v is None:
                continue
            if k in bool_keys:
                coerced[k] = str(v).lower() == "true"
            elif k in int_keys:
                try:
                    coerced[k] = int(v)
                except (ValueError, TypeError):
                    pass
            else:
                coerced[k] = v
        # Return defaults for any keys not yet saved so the form is fully populated
        # on first load and values persist correctly on the first Save.
        defaults = {
            "cc_enabled": False,
            "cc_run_mode": "playlist",
            "cc_period": "1month",
            "cc_min_listens": config.CC_MIN_LISTENS,
            "cc_lookback_days": config.CC_LOOKBACK_DAYS,
            "cc_max_per_cycle": config.CC_MAX_PER_CYCLE,
            "cc_cycle_hours": 168,
            "cc_auto_push_playlist": False,
            "cc_playlist_prefix": "New Music",
            "cc_schedule_weekday": 1,
            "cc_schedule_hour": 8,
            "cc_dry_run": False,
            "nr_ignore_keywords": "",
            "nr_ignore_artists": "",
            "release_cache_refresh_weekday": 4,
            "release_cache_refresh_hour": 6,
        }
        return jsonify({"status": "ok", "config": {**defaults, **coerced}})

    @app.route("/api/cruise-control/config", methods=["POST"])
    def cc_config_save():
        data = request.get_json(silent=True) or {}
        allowed_keys = {
            "cc_enabled", "cc_max_per_cycle", "cc_cycle_hours",
            "cc_min_listens", "cc_period", "cc_lookback_days",
            "cc_auto_push_playlist", "cc_run_mode", "cc_playlist_prefix",
            "cc_schedule_weekday", "cc_schedule_hour",
            "cc_dry_run", "nr_ignore_keywords", "nr_ignore_artists",
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
        rows = cc_store.get_queue(status=status, playlist_name=playlist)
        # Remap DB column names to match QueueItem type in the UI
        items = [
            {
                "id": r.get("id"),
                "artist": r.get("artist_name", ""),
                "album": r.get("album_title", ""),
                "kind": r.get("kind", "album"),
                "status": r.get("status", "pending"),
                "requested_by": r.get("requested_by"),
                "requested_at": r.get("created_at"),
                "release_date": r.get("release_date"),
            }
            for r in rows
        ]
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
        rows = cc_store.get_history(limit=limit)
        # Remap DB column names to match HistoryItem type in the UI
        history = [
            {
                "artist": r.get("artist_name", ""),
                "album": r.get("album_name", ""),
                "status": r.get("acquisition_status", "skipped"),
                "reason": r.get("reason", ""),
                "date": r.get("cycle_date", ""),
            }
            for r in rows
        ]
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
        rows = cc_store.get_playlist(playlist_name=name)
        tracks = [
            {
                "name": r.get("track_name") or r.get("name", ""),
                "artist": r.get("artist_name") or r.get("artist", ""),
                "album": r.get("album_name") or r.get("album"),
                "image": r.get("album_cover_url"),
                "is_owned": bool(r.get("is_owned", 0)),
                "score": r.get("score"),
            }
            for r in (rows or [])
        ]
        return jsonify({"status": "ok", "tracks": tracks})

    @app.route("/api/playlists/<path:name>", methods=["PATCH"])
    def playlists_update(name):
        """Update playlist metadata (auto_sync, mode, max_tracks, source_url)."""
        data = request.get_json(silent=True) or {}
        auto_sync = data.get("auto_sync")
        mode = data.get("mode")
        max_tracks = data.get("max_tracks")
        source_url = data.get("source_url")
        cc_store.update_playlist_meta(
            name,
            auto_sync=bool(auto_sync) if auto_sync is not None else None,
            mode=mode,
            max_tracks=int(max_tracks) if max_tracks is not None else None,
            source_url=source_url,
        )
        return jsonify({"status": "ok"})

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

    @app.route("/api/playlists/<path:name>/rebuild", methods=["POST"])
    def playlists_rebuild(name):
        """Alias for /build — rebuild a taste-based playlist."""
        meta = cc_store.get_playlist_meta(name)
        if not meta:
            cc_store.create_playlist_meta(name, source="taste")
        try:
            tracks = _build_taste_playlist_tracks(name)
            return jsonify({"status": "ok", "track_count": len(tracks),
                            "owned_count": sum(1 for t in tracks if t.get("plex_rating_key"))})
        except Exception as e:
            logger.error("Playlist rebuild failed for '%s': %s", name, e)
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

    @app.route("/api/playlists/<path:name>/sync", methods=["POST"])
    def playlists_sync(name):
        """Alias for /import — re-import playlist from external source."""
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
        ranked = last_fm_client.get_top_artists_ranked(period=period, limit=limit)
        return jsonify({"status": "ok", "artists": ranked, "period": period})

    @app.route("/api/stats/top-tracks")
    def stats_top_tracks():
        period = request.args.get("period", "6month")
        limit = min(int(request.args.get("limit", 50)), 200)
        tracks = last_fm_client.get_top_tracks(period=period, limit=limit)
        return jsonify({"status": "ok", "tracks": tracks, "period": period})

    @app.route("/api/stats/top-albums")
    def stats_top_albums():
        period = request.args.get("period", "6month")
        limit = min(int(request.args.get("limit", 50)), 200)
        albums = last_fm_client.get_top_albums(period=period, limit=limit)
        return jsonify({"status": "ok", "albums": albums, "period": period})

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
        accessible = lr.is_db_accessible()
        # Return non-secret settings only — never return key values
        return jsonify({
            "status": "ok",
            "lastfm_username": config.LASTFM_USERNAME,
            "lastfm_configured": bool(config.LASTFM_API_KEY and config.LASTFM_USERNAME),
            "plex_url": config.PLEX_URL,
            "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
            "soulsync_url": config.SOULSYNC_URL,
            "soulsync_db": config.SOULSYNC_DB,
            "soulsync_db_accessible": accessible,
            # Library backend
            "library_backend": cc_store.get_setting("library_backend") or config.LIBRARY_BACKEND,
            "library_accessible": accessible,
            "library_track_count": lr.get_track_count() if accessible else 0,
            "library_last_synced": cc_store.get_setting("library_last_synced"),
        })

    @app.route("/api/settings/test-lastfm", methods=["POST"])
    def settings_test_lastfm():
        result = last_fm_client.test_connection()
        ok = result.get("status") == "ok"
        msg = result.get("username") if ok else result.get("message", "Connection failed")
        return jsonify({"connected": ok, "message": msg})

    @app.route("/api/settings/test-plex", methods=["POST"])
    def settings_test_plex():
        result = plex_push.test_connection()
        ok = result.get("status") == "ok"
        return jsonify({"connected": ok, "message": result.get("message") if not ok else None})

    @app.route("/api/settings/test-soulsync", methods=["POST"])
    def settings_test_soulsync():
        active_backend = cc_store.get_setting("library_backend") or "soulsync"

        if active_backend == "navidrome":
            return jsonify({"connected": False, "message": "Navidrome not yet implemented"})
        if active_backend == "jellyfin":
            return jsonify({"connected": False, "message": "Jellyfin not yet implemented"})
        if active_backend == "plex":
            import os as _os, sqlite3 as _sq
            db_path = config.LIBRARY_DB
            if not _os.path.exists(db_path):
                return jsonify({"connected": False, "message": "Library DB not synced yet — click Sync Library"})
            try:
                with _sq.connect(db_path) as _c:
                    count = _c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
                return jsonify({"connected": True, "message": f"{count:,} tracks indexed"})
            except Exception as e:
                return jsonify({"connected": False, "message": str(e)})
        # soulsync (default)
        from app.db import soulsync_reader as _ss_reader
        db_available = _ss_reader.is_db_accessible()
        api_status = soulsync_api.test_connection()
        api_ok = api_status.get("status") == "ok"
        ok = db_available or api_ok
        if db_available:
            msg = "DB accessible"
        elif api_ok:
            msg = "API reachable (DB not mounted)"
        else:
            msg = api_status.get("message") or "Not accessible"
        return jsonify({"connected": ok, "message": msg})

    @app.route("/api/settings/test-spotify", methods=["POST"])
    def settings_test_spotify():
        if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
            return jsonify({"connected": False, "message": "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET not set"})
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=config.SPOTIFY_CLIENT_ID,
                client_secret=config.SPOTIFY_CLIENT_SECRET,
            ))
            sp.search(q="test", type="artist", limit=1)
            return jsonify({"connected": True})
        except Exception as e:
            return jsonify({"connected": False, "message": str(e)})

    @app.route("/api/settings/test-fanart", methods=["POST"])
    def settings_test_fanart():
        if not config.FANART_API_KEY:
            return jsonify({"connected": False, "message": "FANART_API_KEY not set — add to .env"})
        # Test with Radiohead's well-known MBID (always in Fanart.tv)
        try:
            import requests as _req
            resp = _req.get(
                "https://webservice.fanart.tv/v3/music/a74b1b7f-71a5-4011-9441-d0b5e4122711",
                params={"api_key": config.FANART_API_KEY},
                timeout=10,
            )
            if resp.status_code == 401:
                return jsonify({"connected": False, "message": "Invalid API key"})
            if resp.status_code == 200:
                return jsonify({"connected": True, "message": "Connected"})
            return jsonify({"connected": False, "message": f"HTTP {resp.status_code}"})
        except Exception as e:
            return jsonify({"connected": False, "message": str(e)})

    # -------------------------------------------------------------------------
    # Library backend
    # -------------------------------------------------------------------------

    @app.route("/api/library/status", methods=["GET"])
    def library_status():
        from app.db import get_library_reader
        lr = get_library_reader()
        accessible = lr.is_db_accessible()
        backend = cc_store.get_setting("library_backend") or config.LIBRARY_BACKEND
        return jsonify({
            "status": "ok",
            "backend": backend,
            "accessible": accessible,
            "track_count": lr.get_track_count() if accessible else 0,
            "last_synced": cc_store.get_setting("library_last_synced"),
        })

    @app.route("/api/library/sync", methods=["POST"])
    def library_sync():
        from app.db import get_library_reader
        try:
            lr = get_library_reader()
            result = lr.sync_library()
            cc_store.set_setting(
                "library_last_synced",
                datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            )
            return jsonify({"status": "ok", **result})
        except NotImplementedError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        except Exception as e:
            logger.warning("library sync failed: %s", e)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/settings/library-backend", methods=["POST"])
    def settings_set_library_backend():
        data = request.get_json() or {}
        backend = data.get("backend", "").lower()
        if backend not in {"soulsync", "plex", "navidrome", "jellyfin"}:
            return jsonify({"status": "error", "message": f"Invalid backend: {backend}"}), 400
        cc_store.set_setting("library_backend", backend)
        return jsonify({"status": "ok", "backend": backend})

    @app.route("/api/settings/clear-history", methods=["POST"])
    def settings_clear_history():
        cc_store.clear_history()
        return jsonify({"status": "ok"})

    @app.route("/api/settings/reset-db", methods=["POST"])
    def settings_reset_db():
        cc_store.reset_db()
        return jsonify({"status": "ok"})

    @app.route("/api/settings/clear-image-cache", methods=["POST"])
    def settings_clear_image_cache():
        cc_store.clear_image_cache()
        return jsonify({"status": "ok", "message": "Image cache cleared"})

    # -------------------------------------------------------------------------
    # Stats — loved artists count
    # -------------------------------------------------------------------------

    @app.route("/api/stats/loved-artists")
    def stats_loved_artists():
        loved = last_fm_client.get_loved_artist_names()
        # Return Artist[] so the Discovery page can render artist tiles
        artists = [{"name": name} for name in sorted(loved)]
        return jsonify({"status": "ok", "artists": artists})

    # -------------------------------------------------------------------------
    # Personal Discovery (stub — engine not yet implemented)
    # -------------------------------------------------------------------------

    @app.route("/api/personal-discovery/run", methods=["POST"])
    def personal_discovery_run():
        """Stub endpoint — Personal Discovery engine not yet implemented."""
        return jsonify([])

    # -------------------------------------------------------------------------
    # Image service
    # -------------------------------------------------------------------------

    @app.route("/api/images/resolve", methods=["POST"])
    def images_resolve():
        from app import image_service
        body = request.get_json(silent=True) or {}
        entity_type = body.get("type", "")
        name = body.get("name", "")
        artist = body.get("artist", "")
        if not entity_type or not name:
            return jsonify({"image_url": ""})
        url, pending = image_service.resolve_image(entity_type, name, artist)
        return jsonify({"image_url": url, "pending": pending})

    # -------------------------------------------------------------------------
    # SPA catch-all — React Router client-side navigation
    # -------------------------------------------------------------------------

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
