"""
discovery_runner.py - Forge Discovery config + lightweight result pipeline.

This keeps API contracts stable while the full discovery engine evolves.
No ORM. All SQL uses ? placeholders.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db import rythmx_store

logger = logging.getLogger(__name__)


def _connect():
    return rythmx_store._connect()


DISCOVERY_DEFAULTS: dict[str, Any] = {
    "closeness": 5,
    "seed_period": "1month",
    "min_scrobbles": 10,
    "max_tracks": 50,
    "run_mode": "build",
    "auto_publish": False,
    "schedule_enabled": False,
    "schedule_weekday": 1,
    "schedule_hour": 8,
    "dry_run": False,
}

_INT_KEYS = {"closeness", "min_scrobbles", "max_tracks", "schedule_weekday", "schedule_hour"}
_BOOL_KEYS = {"auto_publish", "schedule_enabled", "dry_run"}
_SEED_PERIODS = {"7day", "1month", "3month", "6month", "12month", "overall"}
_RUN_MODES = {"build", "fetch"}

_CONFIG_PREFIX = "fd_"
_RESULTS_KEY = "fd_last_results_json"


def _setting_key(api_key: str) -> str:
    return f"{_CONFIG_PREFIX}{api_key}"


def get_config() -> dict[str, Any]:
    """Return discovery config from app_settings, merged with defaults."""
    raw = rythmx_store.get_all_settings()
    cfg: dict[str, Any] = {}
    for key, default in DISCOVERY_DEFAULTS.items():
        val = raw.get(_setting_key(key))
        if val is None:
            cfg[key] = default
        elif key in _BOOL_KEYS:
            cfg[key] = str(val).lower() in ("1", "true", "yes", "on")
        elif key in _INT_KEYS:
            try:
                cfg[key] = int(val)
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = str(val)
    return cfg


def validate_config_updates(updates: dict[str, Any]) -> str | None:
    """Return an error message if updates are invalid, otherwise None."""
    if not isinstance(updates, dict):
        return "Invalid payload; expected object"

    for key, value in updates.items():
        if key not in DISCOVERY_DEFAULTS:
            return f"Unknown config field: {key}"

        if key in _INT_KEYS:
            try:
                iv = int(value)
            except (TypeError, ValueError):
                return f"{key} must be an integer"
            if key == "closeness" and not (1 <= iv <= 9):
                return "closeness must be between 1 and 9"
            if key == "min_scrobbles" and iv < 1:
                return "min_scrobbles must be >= 1"
            if key == "max_tracks" and not (1 <= iv <= 500):
                return "max_tracks must be between 1 and 500"
            if key == "schedule_weekday" and not (0 <= iv <= 6):
                return "schedule_weekday must be between 0 and 6"
            if key == "schedule_hour" and not (0 <= iv <= 23):
                return "schedule_hour must be between 0 and 23"

        if key == "seed_period" and str(value) not in _SEED_PERIODS:
            return f"seed_period must be one of: {', '.join(sorted(_SEED_PERIODS))}"

        if key == "run_mode" and str(value) not in _RUN_MODES:
            return f"run_mode must be one of: {', '.join(sorted(_RUN_MODES))}"

    return None


def save_config(updates: dict[str, Any]) -> None:
    """Persist discovery config keys to app_settings."""
    for key, value in updates.items():
        if key in DISCOVERY_DEFAULTS:
            rythmx_store.set_setting(_setting_key(key), str(value))


def get_results() -> list[dict[str, Any]]:
    """Return the latest saved discovery result set."""
    raw = rythmx_store.get_setting(_RESULTS_KEY)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _discover_from_forge_cache(max_tracks: int) -> list[dict[str, Any]]:
    """
    Return candidate artists from forge_discovered_artists.

    This is intentionally lightweight: it provides useful output without adding
    a new heavy enrichment pass during API-M1.
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    da.name AS artist,
                    da.image_url AS image,
                    COALESCE(da.fans_deezer, 0) AS fans_deezer
                FROM forge_discovered_artists da
                LEFT JOIN lib_artists la ON da.name_lower = la.name_lower
                WHERE la.id IS NULL
                ORDER BY COALESCE(da.fans_deezer, 0) DESC, da.name ASC
                LIMIT ?
                """,
                (max_tracks,),
            ).fetchall()
        return [
            {
                "artist": row["artist"],
                "image": row["image"],
                "reason": "From Forge neighborhood cache",
                "similarity": None,
                "tags": [],
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("discovery: cache query failed, returning empty set: %s", exc)
        return []


def run_discovery_pipeline(config_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Run lightweight discovery and persist results for /forge/discovery/results.
    """
    cfg = get_config()
    if config_override:
        cfg.update({k: v for k, v in config_override.items() if k in DISCOVERY_DEFAULTS})

    max_tracks = int(cfg.get("max_tracks", DISCOVERY_DEFAULTS["max_tracks"]))
    run_mode = str(cfg.get("run_mode", DISCOVERY_DEFAULTS["run_mode"]))

    artists = _discover_from_forge_cache(max_tracks=max_tracks)
    rythmx_store.set_setting(_RESULTS_KEY, json.dumps(artists))

    logger.info("discovery: run complete (mode=%s, artists_found=%d)", run_mode, len(artists))
    return {"artists_found": len(artists), "artists": artists}
