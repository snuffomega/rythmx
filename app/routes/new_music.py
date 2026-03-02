import logging
import threading
from flask import Blueprint, jsonify, request
from app.db import rythmx_store
from app import config
from app.runners import scheduler

logger = logging.getLogger(__name__)

new_music_bp = Blueprint("new_music", __name__)

# Maps backend stage (1-8) to frontend pipeline display step per run mode.
# Build: 5 visible steps (no Queue/Fetch steps); Fetch: 7 visible steps.
_STAGE_MAP = {
    "build":   {1: 1, 2: 2, 3: 3, 4: 4, 5: None, 6: None, 7: 5, 8: 5},
    "fetch":   {1: 1, 2: 2, 3: 3, 4: 4, 5: 5,    6: 6,    7: 7, 8: 7},
    "preview": {1: 1, 2: 2, 3: 3, 4: 4, 5: None, 6: None, 7: None, 8: None},
}


@new_music_bp.route("/api/cruise-control/status")
def get_new_music_status():
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
    cur_stage = raw.get("current_stage")
    cur_mode  = raw.get("current_run_mode") or "build"
    stage_map = _STAGE_MAP.get(cur_mode, _STAGE_MAP["build"])
    display_stage = stage_map.get(cur_stage) if cur_stage else None
    total_stages  = 5 if cur_mode == "build" else (7 if cur_mode == "fetch" else 4)
    return jsonify({
        "status": "ok",
        "state": state,
        "stage": display_stage if is_running else None,
        "total_stages": total_stages if is_running else None,
        "last_run": raw.get("last_run"),
        "summary": summary,
        "error": last_result.get("error"),
    })


def cc_status():
    """Deprecated: Use get_new_music_status(). Target removal: Phase 3."""
    return get_new_music_status()


@new_music_bp.route("/api/cruise-control/config", methods=["GET"])
def get_new_music_config():
    raw = rythmx_store.get_all_settings()
    bool_keys = {"enabled", "auto_push_playlist", "dry_run"}
    int_keys = {
        "min_listens", "lookback_days", "max_per_cycle", "cycle_hours",
        "max_playlist_tracks", "schedule_weekday", "schedule_hour",
        "release_cache_refresh_weekday", "release_cache_refresh_hour",
    }
    config_keys = bool_keys | int_keys | {
        "run_mode", "playlist_prefix", "period",
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
        "enabled": False,
        "run_mode": "build",
        "period": "1month",
        "min_listens": config.MIN_LISTENS,
        "lookback_days": config.LOOKBACK_DAYS,
        "max_per_cycle": config.MAX_PER_CYCLE,
        "cycle_hours": 168,
        "max_playlist_tracks": 50,
        "auto_push_playlist": False,
        "playlist_prefix": "New Music",
        "schedule_weekday": 1,
        "schedule_hour": 8,
        "dry_run": False,
        "nr_ignore_keywords": "",
        "nr_ignore_artists": "",
        "release_cache_refresh_weekday": 4,
        "release_cache_refresh_hour": 6,
    }
    return jsonify({"status": "ok", "config": {**defaults, **coerced}})


def cc_config_get():
    """Deprecated: Use get_new_music_config(). Target removal: Phase 3."""
    return get_new_music_config()


@new_music_bp.route("/api/cruise-control/config", methods=["POST"])
def save_new_music_config():
    data = request.get_json(silent=True) or {}
    allowed_keys = {
        "enabled", "max_per_cycle", "cycle_hours",
        "min_listens", "period", "lookback_days",
        "auto_push_playlist", "run_mode", "playlist_prefix",
        "max_playlist_tracks", "schedule_weekday", "schedule_hour",
        "dry_run", "nr_ignore_keywords", "nr_ignore_artists",
        "release_cache_refresh_weekday", "release_cache_refresh_hour",
    }
    for key, value in data.items():
        if key in allowed_keys:
            rythmx_store.set_setting(key, str(value))
    return jsonify({"status": "ok"})


def cc_config_save():
    """Deprecated: Use save_new_music_config(). Target removal: Phase 3."""
    return save_new_music_config()


@new_music_bp.route("/api/cruise-control/run-now", methods=["POST"])
def run_cycle_now():
    if scheduler.get_status()["is_running"]:
        return jsonify({"status": "error", "message": "A cycle is already running"}), 409
    data = request.get_json(silent=True) or {}
    run_mode = data.get("run_mode", "fetch")
    if run_mode not in ("preview", "build", "fetch"):
        run_mode = "fetch"
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


def cc_run_now():
    """Deprecated: Use run_cycle_now(). Target removal: Phase 3."""
    return run_cycle_now()


@new_music_bp.route("/api/cruise-control/history")
def get_cycle_history():
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = rythmx_store.get_history(limit=limit)
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


def cc_history():
    """Deprecated: Use get_cycle_history(). Target removal: Phase 3."""
    return get_cycle_history()


@new_music_bp.route("/api/release-cache/clear", methods=["POST"])
def release_cache_clear():
    rythmx_store.clear_release_cache()
    return jsonify({"status": "ok", "message": "release cache cleared"})
