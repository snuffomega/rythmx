"""
Helper logic extracted from scheduler.py to reduce monolith size.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.services.enrichment.runner import PipelineRunner

NON_FATAL_SCHEDULER_ERRORS = (
    ImportError,
    AttributeError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    sqlite3.Error,
)


def _is_truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _weekday_now_sunday_zero(now: datetime) -> int:
    # UI convention: Sunday=0..Saturday=6.
    return now.isoweekday() % 7


def _should_run_weekly_schedule(
    *,
    settings: dict,
    enabled_key: str,
    weekday_key: str,
    hour_key: str,
    last_run_key: str,
    now: datetime,
) -> bool:
    if not _is_truthy(settings.get(enabled_key)):
        return False

    weekday = _safe_int(settings.get(weekday_key), -1)
    hour = _safe_int(settings.get(hour_key), -1)
    if not (0 <= weekday <= 6 and 0 <= hour <= 23):
        return False

    if _weekday_now_sunday_zero(now) != weekday or now.hour != hour:
        return False

    last_run_iso = settings.get(last_run_key)
    if not last_run_iso:
        return True

    try:
        last = datetime.fromisoformat(str(last_run_iso))
    except (TypeError, ValueError):
        return True
    return not (last.date() == now.date() and last.hour == now.hour)


def _get_discovered_releases_for_build(store) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.artist_deezer_id,
                da.name        AS artist_name,
                la.id          AS library_artist_id,
                r.title,
                r.record_type,
                r.release_date,
                r.cover_url,
                CASE WHEN lr.id IS NOT NULL THEN 1 ELSE 0 END AS in_library
            FROM forge_discovered_releases r
            JOIN forge_discovered_artists da ON r.artist_deezer_id = da.deezer_id
            LEFT JOIN lib_artists la ON da.name_lower = la.name_lower
            LEFT JOIN lib_releases lr
                ON lr.artist_id = la.id
                AND lower(trim(lr.title)) = lower(trim(r.title))
            ORDER BY r.release_date DESC, da.name ASC
            LIMIT 500
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _queue_new_music_build(store, summary: dict, cfg: dict, logger) -> None:
    releases = _get_discovered_releases_for_build(store)
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    build = store.create_forge_build(
        name=f"New Music {stamp}",
        source="new_music",
        status="ready",
        run_mode="build",
        track_list=releases,
        summary={
            "artists_checked": int(summary.get("artists_checked", 0)),
            "releases_found": int(summary.get("releases_found", len(releases))),
            "nm_period": cfg.get("nm_period"),
            "nm_lookback_days": _safe_int(cfg.get("nm_lookback_days"), 90),
            "scheduled": True,
        },
    )
    logger.info(
        "Forge New Music scheduled run queued build '%s' (%d releases)",
        build.get("id"),
        len(releases),
    )


def _queue_discovery_build(store, summary: dict, cfg: dict, logger) -> None:
    artists = summary.get("artists") if isinstance(summary.get("artists"), list) else []
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    custom_name = str(cfg.get("build_name_override") or "").strip()
    build_name = custom_name or f"Custom Discovery {stamp}"
    build = store.create_forge_build(
        name=build_name,
        source="custom_discovery",
        status="ready",
        run_mode=str(cfg.get("run_mode") or "build"),
        track_list=artists,
        summary={
            "artists_found": int(summary.get("artists_found", len(artists))),
            "built_tracks": int(summary.get("built_tracks", len(artists))),
            "target_tracks": _safe_int(cfg.get("max_tracks"), len(artists)),
            "owned_count": int(summary.get("owned_count", 0)),
            "missing_count": int(summary.get("missing_count", 0)),
            "seed_artists_count": int(summary.get("seed_artists_count", 0)),
            "seed_period": cfg.get("seed_period"),
            "max_tracks": _safe_int(cfg.get("max_tracks"), 50),
            "closeness": _safe_int(cfg.get("closeness"), 5),
            "run_mode": str(cfg.get("run_mode") or "build"),
            "avoid_repeat_tracks": bool(cfg.get("avoid_repeat_tracks", True)),
            "track_repeat_cooldown_days": _safe_int(cfg.get("track_repeat_cooldown_days"), 42),
            "cache_ttl_days": _safe_int(cfg.get("cache_ttl_days"), 30),
            "exclude_owned_artists": bool(cfg.get("exclude_owned_artists", False)),
            "scheduled": True,
        },
    )
    logger.info(
        "Forge Custom Discovery scheduled run queued build '%s' (%d items)",
        build.get("id"),
        len(artists),
    )


def _safe_complete_pipeline_history(store, run_id: int | None, summary: dict, error_msg: str | None, logger) -> None:
    if run_id is None:
        return
    try:
        store.complete_pipeline_run(run_id, summary, error_msg)
    except Exception as exc:
        logger.warning("Scheduled pipeline history completion failed (non-fatal): %s", exc)


def run_forge_scheduler_tick(settings: dict, store, logger) -> bool:
    """
    Execute Forge schedule checks (New Music + Custom Discovery).
    Returns True when at least one scheduled Forge pipeline was executed.
    """
    ran_any = False
    now = datetime.now()

    if _should_run_weekly_schedule(
        settings=settings,
        enabled_key="nm_schedule_enabled",
        weekday_key="nm_schedule_weekday",
        hour_key="nm_schedule_hour",
        last_run_key="nm_schedule_last_run",
        now=now,
    ):
        from app.services.forge import new_music_runner

        cfg = new_music_runner.get_config()
        run_mode = "build"
        run_id: int | None = None
        summary: dict = {}
        error_msg: str | None = None

        try:
            run_id = store.insert_pipeline_run("new_music", run_mode, cfg, triggered_by="schedule")
        except Exception as exc:
            logger.warning("New Music schedule history insert failed (non-fatal): %s", exc)

        try:
            summary = new_music_runner.run_new_music_pipeline()
            _queue_new_music_build(store, summary, cfg, logger)
            logger.info("Forge New Music scheduled run completed")
        except Exception as exc:
            error_msg = str(exc)
            summary = {"status": "error", "message": error_msg}
            logger.warning("Forge New Music scheduled run failed: %s", exc)
        finally:
            store.set_setting("nm_schedule_last_run", now.isoformat())
            _safe_complete_pipeline_history(store, run_id, summary, error_msg, logger)
        ran_any = True

    if _should_run_weekly_schedule(
        settings=settings,
        enabled_key="fd_schedule_enabled",
        weekday_key="fd_schedule_weekday",
        hour_key="fd_schedule_hour",
        last_run_key="fd_schedule_last_run",
        now=now,
    ):
        from app.services.forge import discovery_runner

        cfg = discovery_runner.get_config()
        run_mode = str(cfg.get("run_mode") or "build")
        run_id: int | None = None
        summary: dict = {}
        error_msg: str | None = None

        try:
            run_id = store.insert_pipeline_run("custom_discovery", run_mode, cfg, triggered_by="schedule")
        except Exception as exc:
            logger.warning("Custom Discovery schedule history insert failed (non-fatal): %s", exc)

        try:
            summary = discovery_runner.run_discovery_pipeline()
            _queue_discovery_build(store, summary, cfg, logger)
            logger.info("Forge Custom Discovery scheduled run completed")
        except Exception as exc:
            error_msg = str(exc)
            summary = {"status": "error", "message": error_msg}
            logger.warning("Forge Custom Discovery scheduled run failed: %s", exc)
        finally:
            store.set_setting("fd_schedule_last_run", now.isoformat())
            _safe_complete_pipeline_history(store, run_id, summary, error_msg, logger)
        ran_any = True

    return ran_any


def should_library_sync(settings: dict) -> bool:
    """
    Return True if it's time to run the library auto-pipeline.
    Checks: connections verified, lib_auto_sync enabled, interval elapsed, not already running.
    """
    if PipelineRunner.is_running():
        return False
    if not settings.get("plex_verified_at"):
        return False
    auto_sync = settings.get("lib_auto_sync")
    if auto_sync is not None and str(auto_sync).lower() in ("false", "0", "no"):
        return False
    last_synced = settings.get("library_last_synced")
    if not last_synced:
        return True
    try:
        interval_hours = int(settings.get("lib_sync_interval_hours", 24))
        last = datetime.fromisoformat(last_synced)
        return (datetime.utcnow() - last).total_seconds() >= interval_hours * 3600
    except (TypeError, ValueError):
        return True


def run_acquisition_worker(logger) -> None:
    """Run acquisition queue worker once; non-fatal on errors."""
    try:
        from app.services import acquisition

        acquisition.check_queue()
    except NON_FATAL_SCHEDULER_ERRORS as e:
        logger.warning("Acquisition worker error (non-fatal): %s", e)


def warm_image_cache(logger) -> None:
    """Warm image cache once; non-fatal on errors."""
    try:
        from app.services import image_service as img_service

        img_service.warm_image_cache()
    except NON_FATAL_SCHEDULER_ERRORS as e:
        logger.debug("Image warmer error (non-fatal): %s", e)
