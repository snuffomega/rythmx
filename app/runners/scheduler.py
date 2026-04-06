"""
scheduler.py — background cruise control cycle runner.

Threading-based, same pattern used by SoulSync's wishlist/watchlist timers.
Guards against concurrent cycles with is_running flag.

Cruise Control pipeline (7 stages):
  1. Poll Last.fm — top artists filtered by min-listens threshold
  2. Resolve artist identities — Last.fm name → Deezer/Spotify/MB IDs (cached)
  3. Find new releases — within lookback_days, via music_client provider chain
  4. Owned-check — library platform (Plex/Navidrome/Jellyfin), case-insensitive artist + album name
  5. Build download queue — unowned releases, capped at max_per_cycle
  6. Queue downloads — acquisition worker (stub)
  7. Save history — rythmx.db; playlist from owned candidates
"""
import threading
import logging
from datetime import datetime
from app import config
from app.db import rythmx_store
from app.runners import scheduler_helpers as _scheduler_helpers

logger = logging.getLogger(__name__)

# Module-level state
_is_running = False
_last_run: datetime | None = None
_last_result: dict = {}
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_current_stage: int | None = None   # backend stage 1-8; None when not running
_current_run_mode: str | None = None


def get_status() -> dict:
    return {
        "is_running": _is_running,
        "last_run": _last_run.isoformat() if _last_run else None,
        "last_result": _last_result,
        "enabled": config.SCHEDULER_ENABLED,
        "cycle_hours": config.CYCLE_HOURS,
        "current_stage": _current_stage,
        "current_run_mode": _current_run_mode,
    }


def run_cycle(
    run_mode: str = "fetch",
    force_refresh: bool = False,
    triggered_by: str = "manual",
) -> dict:
    """
    Execute one cruise control cycle.
    run_mode: "preview" | "build" | "fetch"
      preview — scan only, no playlist saved
      build   — scan + build named playlist from owned new releases
      fetch   — build + queue downloads for unowned releases
    force_refresh — bypass 7-day release cache, re-fetch from provider
    triggered_by  — "manual" | "schedule"
    Returns a result summary dict.
    """
    global _is_running, _last_run, _last_result, _current_stage, _current_run_mode

    if _is_running:
        logger.warning("Cruise control cycle already running — skipping")
        return {"status": "skipped", "reason": "already_running"}

    _is_running = True
    _current_run_mode = run_mode
    _last_run = datetime.utcnow()

    config_snapshot = rythmx_store.get_all_settings()
    run_id: int | None = None
    try:
        run_id = rythmx_store.insert_pipeline_run(
            "new_music", run_mode, config_snapshot, triggered_by
        )
    except Exception as _hist_err:
        logger.warning("pipeline_history insert failed (non-fatal): %s", _hist_err)

    error_msg: str | None = None
    try:
        result = _execute_cycle(run_mode=run_mode, force_refresh=force_refresh)
        _last_result = result
        return result
    except Exception as e:
        logger.exception("Cruise control cycle failed: %s", e)
        error_msg = str(e)
        _last_result = {"status": "error", "message": error_msg}
        return _last_result
    finally:
        _is_running = False
        _current_stage = None
        _current_run_mode = None
        if run_id is not None:
            try:
                rythmx_store.complete_pipeline_run(run_id, _last_result, error_msg)
            except Exception as _hist_err:
                logger.warning("pipeline_history complete failed (non-fatal): %s", _hist_err)


def _execute_cycle(run_mode: str = "fetch", force_refresh: bool = False) -> dict:
    """
    Full 7-stage Cruise Control pipeline.
    Imports inline to avoid circular imports.
    run_mode: "preview" | "build" | "fetch"
    """
    global _current_stage
    from app.db import get_library_reader
    library_reader = get_library_reader()
    from app.clients import last_fm_client, plex_push, music_client
    from app.services import identity_resolver
    from datetime import date as _date

    logger.info("Cruise control cycle starting (run_mode=%s, force_refresh=%s)",
                run_mode, force_refresh)

    # Load settings from rythmx.db (user overrides via UI take precedence over config defaults)
    settings = rythmx_store.get_all_settings()

    parsed = _scheduler_helpers.parse_cycle_settings(settings)
    min_listens = parsed["min_listens"]
    lookback_days = parsed["lookback_days"]
    max_per_cycle = parsed["max_per_cycle"]
    period = parsed["period"]
    auto_push = parsed["auto_push"]
    ignore_keywords = parsed["ignore_keywords"]
    ignore_artists = parsed["ignore_artists"]
    allowed_kinds = parsed["allowed_kinds"]
    include_features = parsed["include_features"]

    # -------------------------------------------------------------------------
    # Stage 1 — Last.fm top artists filtered by min_listens
    # -------------------------------------------------------------------------
    _current_stage = 1
    top_artists = last_fm_client.get_top_artists(period=period, limit=200)
    qualified = {name: plays for name, plays in top_artists.items() if plays >= min_listens}
    logger.info("Stage 1: %d artists qualify (min_listens=%d, period=%s)",
                len(qualified), min_listens, period)

    if not qualified:
        logger.warning("No artists met the minimum listen threshold — skipping cycle")
        return {"status": "ok", "message": "no_qualified_artists",
                "artists": 0, "releases_found": 0, "queued": 0}

    # -------------------------------------------------------------------------
    # Stage 2-3 — Resolve identities + get new releases
    # -------------------------------------------------------------------------
    _current_stage = 2
    unique_releases, artists_with_releases = _scheduler_helpers.discover_releases_for_qualified_artists(
        qualified=qualified,
        lookback_days=lookback_days,
        ignore_keywords=ignore_keywords,
        allowed_kinds=allowed_kinds,
        force_refresh=force_refresh,
        library_reader=library_reader,
        store=rythmx_store,
        identity_resolver=identity_resolver,
        music_client=music_client,
        ignore_artists=ignore_artists,
        include_features=include_features,
        logger=logger,
    )

    _current_stage = 3
    logger.info("Stage 2-3: %d unique releases found across %d artists", len(unique_releases), artists_with_releases)


    # -------------------------------------------------------------------------
    # Stage 4 — Owned-check via SoulSync DB
    # -------------------------------------------------------------------------
    _current_stage = 4
    owned_releases, unowned, owned_count = _scheduler_helpers.classify_owned_releases(
        unique_releases=unique_releases,
        library_reader=library_reader,
        store=rythmx_store,
        logger=logger,
    )
    _scheduler_helpers.seed_release_artwork_cache(owned_releases, unowned, rythmx_store)


    # Compute playlist name now so both Stage 6 and Stage 7 share the same value.
    playlist_prefix = settings.get("playlist_prefix", "New Music")
    playlist_name_date = (f"{playlist_prefix}_{_date.today().isoformat()}"
                          if run_mode in ("build", "fetch") else None)

    # -------------------------------------------------------------------------
    # Stage 5-6 - Acquisition queue (cruise mode only)
    # -------------------------------------------------------------------------
    if run_mode == "fetch":
        _current_stage = 5
    queued_count, to_queue = _scheduler_helpers.queue_unowned_releases(
        run_mode=run_mode,
        unowned=unowned,
        max_per_cycle=max_per_cycle,
        playlist_name_date=playlist_name_date,
        store=rythmx_store,
        logger=logger,
    )
    if run_mode == "fetch":
        _current_stage = 6


    # -------------------------------------------------------------------------
    # Stage 7 — Build named playlist (playlist/cruise modes)
    #
    # Owned releases: expanded to individual tracks (have plex_rating_key).
    # Unowned releases: album-level placeholder cards (is_owned=0, no plex_rating_key).
    # Saves to playlist_tracks as "{prefix}_{YYYY-MM-DD}".
    # Dry mode skips playlist creation entirely.
    # -------------------------------------------------------------------------
    _current_stage = 7
    playlist_tracks, plex_playlist_id = _scheduler_helpers.build_named_playlist(
        run_mode=run_mode,
        owned_releases=owned_releases,
        unowned=unowned,
        settings=settings,
        library_reader=library_reader,
        store=rythmx_store,
        music_client=music_client,
        plex_push=plex_push,
        playlist_name_date=playlist_name_date,
        auto_push=auto_push,
        logger=logger,
    )


    # -------------------------------------------------------------------------
    # Stage 8 — Auto-sync: rebuild all auto_sync=1 playlists (playlist/cruise modes)
    #
    # Skipped in dry mode. Each auto_sync playlist is rebuilt in-place using the
    # data already fetched this cycle (owned_releases, top_artists).
    # -------------------------------------------------------------------------
    _current_stage = 8
    _scheduler_helpers.run_stage8_autosync(
        run_mode=run_mode,
        owned_releases=owned_releases,
        top_artists=top_artists,
        settings=settings,
        library_reader=library_reader,
        store=rythmx_store,
        logger=logger,
    )

    # Write history entries for this cycle (dry runs produce no history).
    # Helper handles failure as non-fatal so cycle result still returns.
    _scheduler_helpers.write_cycle_history(
        run_mode=run_mode,
        to_queue=to_queue,
        owned_releases=owned_releases,
        unowned=unowned,
        store=rythmx_store,
        logger=logger,
    )

    queue_stats = rythmx_store.get_queue_stats()
    return {
        "status": "ok",
        "run_mode": run_mode,
        "artists_qualified": len(qualified),
        "releases_found": len(unique_releases),
        "releases_owned": owned_count,
        "releases_unowned": len(unowned),
        "queued": queued_count,
        "failed": 0,
        "playlist_tracks": len(playlist_tracks),
        "playlist_name": playlist_name_date,
        "plex_playlist_id": plex_playlist_id,
        "provider": music_client.get_active_provider(),
        "queue_stats": queue_stats,
    }


def _should_run_cc(settings: dict) -> bool:
    return _scheduler_helpers.should_run_cc(settings)


def _should_library_sync(settings: dict) -> bool:
    return _scheduler_helpers.should_library_sync(settings)


def _loop():
    """Background loop - checks every hour whether a CC cycle should run."""
    while not _stop_event.is_set():
        ran_cc = False
        ran_forge = False
        settings = rythmx_store.get_all_settings()

        # Legacy Cruise Control remains env-gated.
        if config.SCHEDULER_ENABLED:
            ran_cc = _scheduler_helpers.run_scheduler_tick(
                settings=settings,
                run_cycle_fn=run_cycle,
                store=rythmx_store,
                logger=logger,
            )

        # Forge schedules are settings-driven and remain active even when
        # legacy cruise control is disabled.
        ran_forge = _scheduler_helpers.run_forge_scheduler_tick(
            settings=settings,
            store=rythmx_store,
            logger=logger,
        )

        _scheduler_helpers.run_acquisition_worker(logger)

        # Warm image cache during idle hours - no-op if everything is already cached.
        if not ran_cc and not ran_forge:
            _scheduler_helpers.warm_image_cache(logger)
        _stop_event.wait(timeout=3600)  # Check every hour


def start():
    """Start the background scheduler thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="cc-scheduler")
    _thread.start()
    if config.SCHEDULER_ENABLED:
        logger.info(
            "Background scheduler started (cruise enabled, interval=%dh)",
            config.CYCLE_HOURS,
        )
    else:
        logger.info(
            "Background maintenance thread started (cruise disabled; Forge schedules + acquisition/image warmer active)"
        )


def stop():
    """Signal the background thread to stop."""
    _stop_event.set()
    logger.info("Cruise control scheduler stop requested")






