import logging
import threading
from flask import Blueprint, jsonify, request
from app.db import cc_store
from app import config, scheduler

logger = logging.getLogger(__name__)

new_music_bp = Blueprint("new_music", __name__)


@new_music_bp.route("/api/cruise-control/status")
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


@new_music_bp.route("/api/cruise-control/config", methods=["GET"])
def cc_config_get():
    raw = cc_store.get_all_settings()
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


@new_music_bp.route("/api/cruise-control/config", methods=["POST"])
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


@new_music_bp.route("/api/cruise-control/run-now", methods=["POST"])
def cc_run_now():
    if scheduler.get_status()["is_running"]:
        return jsonify({"status": "error", "message": "A cycle is already running"}), 409
    data = request.get_json(silent=True) or {}
    run_mode = data.get("run_mode", "cruise")
    if run_mode not in ("dry", "playlist", "cruise"):
        run_mode = "cruise"
    force_refresh = bool(data.get("force_refresh", False))
    t = threading.Thread(
        target=scheduler.run_cycle,
        args=(run_mode,),
        kwargs={"force_refresh": force_refresh},
        daemon=True,
        name="cc-manual-run",
    )
    t.start()
    return jsonify({"status": "ok", "message": "cycle_started", "run_mode": run_mode})


@new_music_bp.route("/api/cruise-control/history")
def cc_history():
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = cc_store.get_history(limit=limit)
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


@new_music_bp.route("/api/release-cache/clear", methods=["POST"])
def release_cache_clear():
    cc_store.clear_release_cache()
    return jsonify({"status": "ok", "message": "release cache cleared"})
