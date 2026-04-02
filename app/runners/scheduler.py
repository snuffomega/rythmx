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
import re
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

    min_listens = int(settings.get("min_listens", config.MIN_LISTENS))
    lookback_days = int(settings.get("lookback_days", config.LOOKBACK_DAYS))
    max_per_cycle = int(settings.get("max_per_cycle", config.MAX_PER_CYCLE))
    period = settings.get("period", "1month")
    auto_push = settings.get("auto_push_playlist", "false") == "true"
    ignore_kw_raw = settings.get("nr_ignore_keywords", "") or config.IGNORE_KEYWORDS
    ignore_keywords = [k.strip() for k in ignore_kw_raw.split(",") if k.strip()]
    # Normalize: lowercase + strip punctuation so "Ballyhoo!" matches "ballyhoo"
    _strip_punct = lambda s: re.sub(r"[^\w\s]", "", s).strip()
    ignore_artists = {_strip_punct(a.strip().lower()) for a in settings.get("nr_ignore_artists", "").split(",") if a.strip()}
    release_kinds_raw = settings.get("release_kinds") or config.RELEASE_KINDS
    allowed_kinds = {k.strip().lower() for k in release_kinds_raw.split(",") if k.strip()}
    include_features_raw = settings.get("include_features")
    include_features = (
        True if include_features_raw is None
        else str(include_features_raw).lower() not in ("false", "0", "no")
    )

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
    all_releases = []
    artists_with_releases = 0

    for artist_name in qualified:
        # Start from cached IDs if available
        cached = rythmx_store.get_cached_artist(artist_name) or {}

        # --- Confidence-based identity resolution (Last.fm ↔ iTunes top-track overlap) ---
        # Returns cache hit immediately if confidence >= 85 and resolved < 30 days ago.
        # On a fresh artist: fetches Last.fm + iTunes top tracks, scores overlap, caches result.
        identity = identity_resolver.resolve_artist(artist_name)
        identity_itunes_id = identity.get("itunes_artist_id")
        if identity_itunes_id and not cached.get("itunes_artist_id"):
            cached["itunes_artist_id"] = identity_itunes_id
        logger.debug(
            "Identity: %s → iTunes:%s (confidence=%d, method=%s)",
            artist_name, identity_itunes_id or "none",
            identity.get("confidence", 0),
            (identity.get("reason_codes") or ["?"])[-1],
        )

        # Enrich with pre-resolved IDs from the library backend (Plex/SoulSync).
        # Reusing cached IDs skips external API search calls entirely.
        # get_native_artist_id() returns the backend's internal PK for track expansion.
        sp_artist_id = library_reader.get_spotify_artist_id(artist_name)
        dz_artist_id = library_reader.get_deezer_artist_id(artist_name)
        it_artist_id = library_reader.get_itunes_artist_id(artist_name)
        ss_artist_id = library_reader.get_native_artist_id(artist_name)

        if sp_artist_id and not cached.get("spotify_artist_id"):
            cached["spotify_artist_id"] = sp_artist_id
        if dz_artist_id and not cached.get("deezer_artist_id"):
            cached["deezer_artist_id"] = dz_artist_id
        if it_artist_id and not cached.get("itunes_artist_id"):
            cached["itunes_artist_id"] = it_artist_id
        if ss_artist_id and not cached.get("soulsync_artist_id"):
            cached["soulsync_artist_id"] = ss_artist_id

        releases, resolved_ids = music_client.get_new_releases_for_artist(
            artist_name=artist_name,
            days_ago=lookback_days,
            ignore_keywords=ignore_keywords,
            cached_ids=cached,
            spotify_artist_id=sp_artist_id,
            force_refresh=force_refresh,
            allowed_kinds=allowed_kinds,
        )

        # Write resolved IDs back to cache so subsequent cycles skip API searches.
        # COALESCE upsert — only fills in missing values, never overwrites good IDs.
        if resolved_ids or ss_artist_id:
            rythmx_store.cache_artist(
                lastfm_name=artist_name,
                deezer_artist_id=resolved_ids.get("deezer_artist_id"),
                spotify_artist_id=resolved_ids.get("spotify_artist_id"),
                itunes_artist_id=resolved_ids.get("itunes_artist_id"),
                mb_artist_id=resolved_ids.get("mb_artist_id"),
                soulsync_artist_id=ss_artist_id,
                # Use identity confidence if we have it; fall back to 90 for SoulSync-sourced IDs
                confidence=identity.get("confidence", 90),
            )

        if releases:
            # Backfill artist name for sources (MusicBrainz) that may leave it blank
            for r in releases:
                if not r.artist:
                    r.artist = artist_name
            all_releases.extend(releases)
            artists_with_releases += 1

    # Deduplicate by normalized artist + title; apply ignore filters
    seen = set()
    unique_releases = []
    for r in all_releases:
        if ignore_artists and _strip_punct(r.artist.lower()) in ignore_artists:
            logger.debug("Ignoring artist: %s", r.artist)
            continue
        if ignore_keywords and any(kw in r.title.lower() for kw in ignore_keywords):
            logger.debug("Ignoring release (keyword match): %s — %s", r.artist, r.title)
            continue
        key = (music_client.norm(r.artist), music_client.norm(r.title))
        if key not in seen:
            seen.add(key)
            unique_releases.append(r)

    if not include_features:
        _FEAT_RE = re.compile(r'\b(feat\.?|ft\.?|featuring)\b|\(with ', re.IGNORECASE)
        before = len(unique_releases)
        unique_releases = [r for r in unique_releases if not _FEAT_RE.search(r.title)]
        filtered = before - len(unique_releases)
        if filtered:
            logger.info("Stage 2-3: filtered %d feature/collab release(s) (include_features=false)",
                        filtered)

    _current_stage = 3
    logger.info("Stage 2-3: %d unique releases found across %d artists",
                len(unique_releases), artists_with_releases)

    # -------------------------------------------------------------------------
    # Stage 4 — Owned-check via SoulSync DB
    # -------------------------------------------------------------------------
    _current_stage = 4
    owned_releases = []   # Release objects that are in the library (for Stage 7 playlist)
    unowned = []
    owned_count = 0

    for r in unique_releases:
        # Use cached native artist ID for PK-based owned-check tiers
        cached_r = rythmx_store.get_cached_artist(r.artist) or {}
        ss_id = cached_r.get("soulsync_artist_id") or library_reader.get_native_artist_id(r.artist)
        sp_id = library_reader.get_spotify_artist_id(r.artist)
        it_id = cached_r.get("itunes_artist_id") or library_reader.get_itunes_artist_id(r.artist)
        rating_key = library_reader.check_album_owned(
            r.artist, r.title,
            soulsync_artist_id=ss_id,
            spotify_artist_id=sp_id,
            itunes_artist_id=it_id,
            deezer_album_id=r.deezer_album_id or None,
            spotify_album_id=r.spotify_album_id or None,
            itunes_album_id=r.itunes_album_id or None,
        )
        if rating_key:
            owned_count += 1
            owned_releases.append(r)
        else:
            unowned.append(r)

    logger.info("Stage 4: %d owned, %d unowned", owned_count, len(unowned))

    # Seed image cache with artwork iTunes already returned during release discovery.
    # This means Discovery new-releases shelf shows art instantly on next page load
    # instead of triggering a second iTunes lookup per album.
    for r in owned_releases + unowned:
        if r.artwork_url:
            _img_key = f"{r.artist.lower()}|||{r.title.lower()}"
            rythmx_store.set_image_cache("album", _img_key, r.artwork_url)

    # Compute playlist name now so both Stage 6 and Stage 7 share the same value.
    playlist_prefix = settings.get("playlist_prefix", "New Music")
    playlist_name_date = (f"{playlist_prefix}_{_date.today().isoformat()}"
                          if run_mode in ("build", "fetch") else None)

    # -------------------------------------------------------------------------
    # Stage 5-6 — Acquisition queue (cruise mode only)
    #
    # Writes unowned releases to download_queue (provider-agnostic).
    # is_in_queue() blocks only 'pending'/'submitted' — 'found'/'failed' are
    # re-evaluatable.  Future-dated (pre-announced) releases are never queued.
    # -------------------------------------------------------------------------
    queued_count = 0
    to_queue = []

    if run_mode == "fetch":
        _current_stage = 5
        # Sort by release_date descending (newest first)
        unowned.sort(key=lambda r: r.release_date, reverse=True)
        today_str = _date.today().isoformat()
        new_unowned = [
            r for r in unowned
            if not rythmx_store.is_in_queue(r.artist, r.title)
            and (r.release_date or "9999") <= today_str
        ]
        skipped_count = len(unowned) - len(new_unowned)
        if skipped_count:
            logger.info("Stage 5: skipped %d releases already in acquisition queue", skipped_count)
        to_queue = new_unowned[:max_per_cycle]
        logger.info("Stage 5: %d releases selected for acquisition (cap=%d)",
                    len(to_queue), max_per_cycle)

        _current_stage = 6
        for r in to_queue:
            queue_id = rythmx_store.add_to_queue(
                artist_name=r.artist, album_title=r.title,
                release_date=r.release_date, kind=r.kind, source=r.source,
                itunes_album_id=r.itunes_album_id or None,
                deezer_album_id=r.deezer_album_id or None,
                spotify_album_id=r.spotify_album_id or None,
                requested_by="cc", playlist_name=playlist_name_date,
            )
            queued_count += 1
            logger.info("Stage 6: queued '%s \u2014 %s' (queue_id=%d)", r.artist, r.title, queue_id)
        logger.info("Stage 6: %d releases added to acquisition queue", queued_count)
    else:
        logger.info("Stage 5-6: skipped (not fetch mode, run_mode=%s)", run_mode)

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
    if run_mode in ("build", "fetch"):
        auto_playlists = [p for p in rythmx_store.list_playlists() if p.get("auto_sync")]
        logger.info("Stage 8: %d auto-sync playlist(s) to rebuild", len(auto_playlists))
        for pl in auto_playlists:
            _auto_sync_playlist(pl, owned_releases, top_artists, settings, library_reader)
    else:
        logger.info("Stage 8: skipped (run_mode=preview)")

    # Write history entries for this cycle (dry runs produce no history)
    # Wrapped in its own try-except — history failure is non-fatal and must not
    # corrupt the returned result dict or prevent the cycle from completing.
    if run_mode != "preview":
        try:
            queued_keys = {(r.artist, r.title) for r in to_queue}
            for r in owned_releases:
                rythmx_store.add_history_entry(
                    {"artist_name": r.artist, "album_name": r.title}, status="owned"
                )
            for r in unowned:
                if (r.artist, r.title) in queued_keys:
                    # Newly queued this run
                    entry_status, entry_reason = "queued", ""
                elif run_mode == "fetch" and rythmx_store.is_in_queue(r.artist, r.title):
                    # Already pending/submitted in queue from a prior cruise run
                    entry_status, entry_reason = "queued", "already_queued"
                else:
                    entry_status = "skipped"
                    entry_reason = "build_mode" if run_mode == "build" else ""
                rythmx_store.add_history_entry(
                    {"artist_name": r.artist, "album_name": r.title},
                    status=entry_status, reason=entry_reason
                )
        except Exception as e:
            logger.warning("History write failed (non-fatal): %s", e)

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


def _auto_sync_playlist(pl, owned_releases, top_artists, settings, library_reader):
    _scheduler_helpers.auto_sync_playlist(
        pl=pl,
        owned_releases=owned_releases,
        top_artists=top_artists,
        settings=settings,
        library_reader=library_reader,
        store=rythmx_store,
        logger=logger,
    )


def _should_run_cc(settings: dict) -> bool:
    return _scheduler_helpers.should_run_cc(settings)


def _should_library_sync(settings: dict) -> bool:
    return _scheduler_helpers.should_library_sync(settings)


def _loop():
    """Background loop — checks every hour whether a CC cycle should run."""
    while not _stop_event.is_set():
        ran_cc = False
        if config.SCHEDULER_ENABLED:
            settings = rythmx_store.get_all_settings()
            if _should_run_cc(settings):
                mode = settings.get("run_mode", "fetch")
                run_cycle(run_mode=mode, force_refresh=False, triggered_by="schedule")
                rythmx_store.set_setting("last_run", datetime.now().isoformat())
                ran_cc = True
            # Library auto-pipeline — runs independently of CC cycle
            if _should_library_sync(settings):
                try:
                    from app.services.enrichment.runner import PipelineRunner
                    threading.Thread(
                        target=PipelineRunner().run,
                        kwargs={"on_progress": None},
                        daemon=True,
                        name="lib-pipeline",
                    ).start()
                    logger.info("Library auto-pipeline triggered by scheduler")
                except Exception as e:
                    logger.warning("Library auto-pipeline launch failed: %s", e)
        # Run acquisition worker every loop regardless of whether CC ran
        try:
            from app.services import acquisition
            acquisition.check_queue()
        except Exception as e:
            logger.warning("Acquisition worker error (non-fatal): %s", e)
        # Warm image cache during idle hours — no-op if everything is already cached
        if not ran_cc:
            try:
                from app.services import image_service as _img_svc
                _img_svc.warm_image_cache()
            except Exception as e:
                logger.debug("Image warmer error (non-fatal): %s", e)
        _stop_event.wait(timeout=3600)  # Check every hour


def start():
    """Start the background scheduler thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="cc-scheduler")
    _thread.start()
    logger.info("Cruise control scheduler started (interval=%dh)", config.CYCLE_HOURS)


def stop():
    """Signal the background thread to stop."""
    _stop_event.set()
    logger.info("Cruise control scheduler stop requested")



