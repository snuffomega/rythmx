"""
scheduler.py — background cruise control cycle runner.

Threading-based, same pattern used by SoulSync's wishlist/watchlist timers.
Guards against concurrent cycles with is_running flag.

Cruise Control pipeline (7 stages):
  1. Poll Last.fm — top artists filtered by min-listens threshold
  2. Resolve artist identities — Last.fm name → Deezer/Spotify/MB IDs (cached)
  3. Find new releases — within cc_lookback_days, via music_client provider chain
  4. Owned-check — SoulSync DB (case-insensitive artist + album name)
  5. Build download queue — unowned releases, capped at cc_max_per_cycle
  6. Queue downloads — SoulSync API (or dry-run)
  7. Save history — cc.db; playlist from owned candidates
"""
import threading
import logging
from datetime import datetime
from app import config
from app.db import cc_store

logger = logging.getLogger(__name__)

# Module-level state
_is_running = False
_last_run: datetime | None = None
_last_result: dict = {}
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def get_status() -> dict:
    return {
        "is_running": _is_running,
        "last_run": _last_run.isoformat() if _last_run else None,
        "last_result": _last_result,
        "enabled": config.CC_ENABLED,
        "cycle_hours": config.CC_CYCLE_HOURS,
    }


def run_cycle(run_mode: str = "cruise", force_refresh: bool = False) -> dict:
    """
    Execute one cruise control cycle.
    run_mode: "dry" | "playlist" | "cruise"
      dry      — scan only, no playlist saved
      playlist — scan + build named playlist from owned new releases
      cruise   — playlist + queue downloads for unowned releases
    force_refresh — bypass 7-day release cache, re-fetch from provider
    Returns a result summary dict.
    """
    global _is_running, _last_run, _last_result

    if _is_running:
        logger.warning("Cruise control cycle already running — skipping")
        return {"status": "skipped", "reason": "already_running"}

    _is_running = True
    _last_run = datetime.utcnow()

    try:
        result = _execute_cycle(run_mode=run_mode, force_refresh=force_refresh)
        _last_result = result
        return result
    except Exception as e:
        logger.exception("Cruise control cycle failed: %s", e)
        _last_result = {"status": "error", "message": str(e)}
        return _last_result
    finally:
        _is_running = False


def _execute_cycle(run_mode: str = "cruise", force_refresh: bool = False) -> dict:
    """
    Full 7-stage Cruise Control pipeline.
    Imports inline to avoid circular imports.
    run_mode: "dry" | "playlist" | "cruise"
    """
    from app.db import get_library_reader
    soulsync_reader = get_library_reader()
    from app import last_fm_client, plex_push, music_client, identity_resolver
    from datetime import date as _date

    logger.info("Cruise control cycle starting (run_mode=%s, force_refresh=%s)",
                run_mode, force_refresh)

    # Load settings from cc.db (user overrides via UI take precedence over config defaults)
    settings = cc_store.get_all_settings()

    if force_refresh:
        cc_store.clear_release_cache()
        logger.info("Stage 2-3: release cache cleared (force_refresh=True)")
    min_listens = int(settings.get("cc_min_listens", config.CC_MIN_LISTENS))
    lookback_days = int(settings.get("cc_lookback_days", config.CC_LOOKBACK_DAYS))
    max_per_cycle = int(settings.get("cc_max_per_cycle", config.CC_MAX_PER_CYCLE))
    period = settings.get("cc_period", "6month")
    auto_push = settings.get("cc_auto_push_playlist", "false") == "true"
    ignore_keywords = [k.strip() for k in config.CC_IGNORE_KEYWORDS.split(",") if k.strip()]

    # -------------------------------------------------------------------------
    # Stage 1 — Last.fm top artists filtered by min_listens
    # -------------------------------------------------------------------------
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
    all_releases = []
    artists_with_releases = 0

    for artist_name in qualified:
        # Start from cached IDs if available
        cached = cc_store.get_cached_artist(artist_name) or {}

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
            identity.get("reason_codes", ["?"])[-1],
        )

        # Enrich with pre-resolved IDs from SoulSync's own artists table.
        # For library artists SoulSync has already resolved iTunes/Deezer/MB IDs —
        # reusing these skips external API search calls entirely.
        # Also resolve the SoulSync internal artist ID for exact PK joins in owned-check.
        sp_artist_id = soulsync_reader.get_spotify_artist_id(artist_name)
        dz_artist_id = soulsync_reader.get_deezer_artist_id(artist_name)
        it_artist_id = soulsync_reader.get_itunes_artist_id(artist_name)
        ss_artist_id = soulsync_reader.get_soulsync_artist_id(artist_name)

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
        )

        # Write resolved IDs back to cache so subsequent cycles skip API searches.
        # COALESCE upsert — only fills in missing values, never overwrites good IDs.
        if resolved_ids or ss_artist_id:
            cc_store.cache_artist(
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

    # Deduplicate by normalized artist + title
    seen = set()
    unique_releases = []
    for r in all_releases:
        key = (music_client.norm(r.artist), music_client.norm(r.title))
        if key not in seen:
            seen.add(key)
            unique_releases.append(r)

    logger.info("Stage 2-3: %d unique releases found across %d artists",
                len(unique_releases), artists_with_releases)

    # -------------------------------------------------------------------------
    # Stage 4 — Owned-check via SoulSync DB
    # -------------------------------------------------------------------------
    owned_releases = []   # Release objects that are in the library (for Stage 7 playlist)
    unowned = []
    owned_count = 0

    for r in unique_releases:
        # Use cached SoulSync artist ID for Tier 0 PK join (most reliable)
        cached_r = cc_store.get_cached_artist(r.artist) or {}
        ss_id = cached_r.get("soulsync_artist_id") or soulsync_reader.get_soulsync_artist_id(r.artist)
        sp_id = soulsync_reader.get_spotify_artist_id(r.artist)
        it_id = cached_r.get("itunes_artist_id") or soulsync_reader.get_itunes_artist_id(r.artist)
        rating_key = soulsync_reader.check_album_owned(
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

    # -------------------------------------------------------------------------
    # Stage 5-6 — Download queue (cruise mode only)
    # -------------------------------------------------------------------------
    queued_count = 0

    if run_mode == "cruise":
        # Sort by release_date descending (newest first)
        unowned.sort(key=lambda r: r.release_date, reverse=True)
        today_str = _date.today().isoformat()
        # Skip releases already identified in a previous cycle, and never queue
        # future-dated (pre-announced) releases for download.
        new_unowned = [
            r for r in unowned
            if not cc_store.is_release_in_history(r.artist, r.title)
            and (r.release_date or "9999") <= today_str
        ]
        skipped_count = len(unowned) - len(new_unowned)
        if skipped_count:
            logger.info("Stage 5: skipped %d already-identified releases", skipped_count)
        to_queue = new_unowned[:max_per_cycle]
        logger.info("Stage 5: %d releases selected for acquisition (cap=%d)",
                    len(to_queue), max_per_cycle)

        # Stage 6 — mark as identified (download integration pending)
        # TODO: POST /api/watchlist/add to SoulSync once endpoint is confirmed.
        for r in to_queue:
            queued_count += 1
            cc_store.add_history_entry(
                {
                    "track_name": r.title,
                    "artist_name": r.artist,
                    "album_name": r.title,
                    "source": r.source,
                    "score": None,
                },
                status="identified",
                reason=f"release_date={r.release_date} kind={r.kind} provider={r.source}",
            )
        logger.info("Stage 6: %d releases identified for acquisition (download integration pending)",
                    queued_count)
    else:
        logger.info("Stage 5-6: skipped (run_mode=%s)", run_mode)

    # -------------------------------------------------------------------------
    # Stage 7 — Build named playlist from owned new releases (playlist/cruise modes)
    #
    # Expands each owned release to individual tracks via get_tracks_for_album().
    # Saves to cc_playlist as "New Music_YYYY-MM-DD" (prefix configurable).
    # Dry mode skips playlist creation entirely.
    # -------------------------------------------------------------------------
    playlist_tracks = []
    plex_playlist_id = None
    playlist_name_date = None

    if run_mode in ("playlist", "cruise"):
        playlist_prefix = settings.get("cc_playlist_prefix", "New Music")
        playlist_name_date = f"{playlist_prefix}_{_date.today().isoformat()}"

        try:
            for r in owned_releases:
                cached_r = cc_store.get_cached_artist(r.artist) or {}
                ss_id = (cached_r.get("soulsync_artist_id")
                         or soulsync_reader.get_soulsync_artist_id(r.artist))
                if ss_id:
                    tracks = soulsync_reader.get_tracks_for_album(ss_id, r.title)
                    for t in tracks:
                        playlist_tracks.append({
                            "plex_rating_key": t["plex_rating_key"],
                            "track_name": t["track_title"],
                            "artist_name": r.artist,
                            "album_name": r.title,
                            "album_cover_url": t.get("album_thumb_url") or "",
                            "score": None,
                        })
                else:
                    logger.debug("Stage 7: no SoulSync ID for owned release artist '%s'", r.artist)

            cc_store.create_playlist_meta(playlist_name_date, source="cc", mode="cc_new_music")
            cc_store.save_playlist(playlist_tracks, playlist_name=playlist_name_date)
            cc_store.mark_playlist_synced(playlist_name_date)
            logger.info("Stage 7: playlist '%s' saved with %d tracks",
                        playlist_name_date, len(playlist_tracks))

            if auto_push and playlist_tracks:
                rating_keys = [t["plex_rating_key"] for t in playlist_tracks
                               if t.get("plex_rating_key")]
                if rating_keys:
                    plex_playlist_id = plex_push.create_or_update_playlist(
                        playlist_name_date, rating_keys)
                    if plex_playlist_id:
                        cc_store.update_playlist_plex_id(playlist_name_date, plex_playlist_id)

        except Exception as e:
            logger.warning("Stage 7 playlist build failed (non-fatal): %s", e)
    else:
        logger.info("Stage 7: skipped (run_mode=dry)")

    # -------------------------------------------------------------------------
    # Stage 8 — Auto-sync: rebuild all auto_sync=1 playlists (playlist/cruise modes)
    #
    # Skipped in dry mode. Each auto_sync playlist is rebuilt in-place using the
    # data already fetched this cycle (owned_releases, top_artists).
    # -------------------------------------------------------------------------
    if run_mode in ("playlist", "cruise"):
        auto_playlists = [p for p in cc_store.list_playlists() if p.get("auto_sync")]
        logger.info("Stage 8: %d auto-sync playlist(s) to rebuild", len(auto_playlists))
        for pl in auto_playlists:
            _auto_sync_playlist(pl, owned_releases, top_artists, settings, soulsync_reader)
    else:
        logger.info("Stage 8: skipped (run_mode=dry)")

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
    }


def _auto_sync_playlist(pl, owned_releases, top_artists, settings, soulsync_reader):
    """
    Rebuild a single auto_sync playlist in-place.

    Dispatches by source:
      cc     — re-expand owned_releases to tracks using current library state
      taste  — rebuild using latest Last.fm top artists + current library
      deezer / spotify / lastfm — re-import from source_url
    """
    from app.db import cc_store as _cc_store
    from app import last_fm_client, engine

    name = pl.get("name") or pl.get("playlist_name")
    source = pl.get("source", "")

    try:
        if source == "cc":
            # Re-expand owned releases to tracks (same logic as Stage 7, but in-place)
            playlist_tracks = []
            for r in owned_releases:
                cached_r = _cc_store.get_cached_artist(r.artist) or {}
                ss_id = (cached_r.get("soulsync_artist_id")
                         or soulsync_reader.get_soulsync_artist_id(r.artist))
                if ss_id:
                    tracks = soulsync_reader.get_tracks_for_album(ss_id, r.title)
                    for t in tracks:
                        playlist_tracks.append({
                            "plex_rating_key": t["plex_rating_key"],
                            "track_name": t["track_title"],
                            "artist_name": r.artist,
                            "album_name": r.title,
                            "album_cover_url": t.get("album_thumb_url") or "",
                            "score": None,
                        })
            _cc_store.save_playlist(playlist_tracks, playlist_name=name)
            _cc_store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced cc playlist '%s' (%d tracks)", name, len(playlist_tracks))

        elif source == "taste":
            # Rebuild taste playlist using top_artists already fetched in Stage 1
            meta = _cc_store.get_playlist_meta(name) or {}
            max_tracks = int(meta.get("max_tracks") or 50)
            max_per_artist = int(meta.get("max_per_artist") or 2)
            loved = last_fm_client.get_loved_artist_names()

            artist_tracks = {}
            for artist_name in top_artists:
                cached = _cc_store.get_cached_artist(artist_name) or {}
                ss_id = cached.get("soulsync_artist_id") or soulsync_reader.get_soulsync_artist_id(artist_name)
                if ss_id:
                    tracks = soulsync_reader.get_all_tracks_for_artist(ss_id)
                    if tracks:
                        artist_tracks[artist_name] = tracks

            scored = engine.build_taste_playlist(
                top_artists, loved, artist_tracks,
                limit=max_tracks, max_per_artist=max_per_artist,
            )
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
            _cc_store.save_playlist(to_save, playlist_name=name)
            _cc_store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced taste playlist '%s' (%d tracks)", name, len(to_save))

        elif source in ("spotify", "lastfm", "deezer"):
            from app import playlist_importer
            source_url = pl.get("source_url") or ""
            if not source_url:
                logger.warning("Stage 8: skipping '%s' — no source_url stored", name)
                return
            if source == "spotify":
                playlist_importer.import_from_spotify(source_url, playlist_name=name)
            elif source == "lastfm":
                playlist_importer.import_from_lastfm(source_url, playlist_name=name)
            elif source == "deezer":
                playlist_importer.import_from_deezer(source_url, playlist_name=name)
            logger.info("Stage 8: auto-synced %s playlist '%s'", source, name)

        else:
            logger.debug("Stage 8: no auto-sync handler for source='%s' (playlist='%s')", source, name)

    except Exception as e:
        logger.warning("Stage 8: auto-sync failed for playlist '%s': %s", name, e)


def _should_weekly_refresh(settings: dict) -> bool:
    """
    Return True if it's time for the weekly release cache refresh.
    Default: Thursday (weekday=3) at 05:00 UTC.
    Checks cc_settings['release_cache_last_cleared_weekday'] to avoid multiple
    refreshes in the same day.
    """
    weekday = int(settings.get("release_cache_refresh_weekday", "3"))
    hour = int(settings.get("release_cache_refresh_hour", "5"))
    now = datetime.utcnow()
    if now.weekday() != weekday or now.hour < hour:
        return False
    last_cleared = cc_store.get_setting("release_cache_last_cleared_weekday") or ""
    return last_cleared != now.date().isoformat()


def _loop():
    """Background loop — runs a cycle every CC_CYCLE_HOURS hours."""
    interval_secs = config.CC_CYCLE_HOURS * 3600
    while not _stop_event.is_set():
        if config.CC_ENABLED:
            settings = cc_store.get_all_settings()
            mode = settings.get("cc_run_mode", "cruise")
            force = _should_weekly_refresh(settings)
            if force:
                cc_store.set_setting("release_cache_last_cleared_weekday",
                                     datetime.utcnow().date().isoformat())
                logger.info("Weekly release cache refresh triggered (weekday=%s, hour=%s)",
                            settings.get("release_cache_refresh_weekday", "3"),
                            settings.get("release_cache_refresh_hour", "5"))
            run_cycle(run_mode=mode, force_refresh=force)
        _stop_event.wait(timeout=interval_secs)


def start():
    """Start the background scheduler thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="cc-scheduler")
    _thread.start()
    logger.info("Cruise control scheduler started (interval=%dh)", config.CC_CYCLE_HOURS)


def stop():
    """Signal the background thread to stop."""
    _stop_event.set()
    logger.info("Cruise control scheduler stop requested")
