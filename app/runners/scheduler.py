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

    if force_refresh:
        rythmx_store.clear_release_cache()
        logger.info("Stage 2-3: release cache cleared (force_refresh=True)")
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
    playlist_tracks = []
    plex_playlist_id = None

    if run_mode in ("build", "fetch"):
        try:
            # Owned: expand each album to individual tracks
            for r in owned_releases:
                cached_r = rythmx_store.get_cached_artist(r.artist) or {}
                ss_id = (cached_r.get("soulsync_artist_id")
                         or library_reader.get_native_artist_id(r.artist))
                if ss_id:
                    tracks = library_reader.get_tracks_for_album(ss_id, r.title)
                    for t in tracks:
                        playlist_tracks.append({
                            "plex_rating_key": t["plex_rating_key"],
                            "track_name": t["track_title"],
                            "artist_name": r.artist,
                            "album_name": r.title,
                            "album_cover_url": t.get("album_thumb_url") or "",
                            "score": None,
                            "is_owned": 1,
                            "release_date": r.release_date,
                        })
                else:
                    logger.debug("Stage 7: no native artist ID for owned release artist '%s'", r.artist)

            # Unowned: album-level placeholder (shown as "Missing" in playlist UI)
            for r in unowned:
                playlist_tracks.append({
                    "plex_rating_key": None,
                    "track_name": r.title,
                    "artist_name": r.artist,
                    "album_name": r.title,
                    "album_cover_url": "",
                    "score": None,
                    "is_owned": 0,
                    "release_date": r.release_date,
                })

            # Seed from download_queue: items previously queued that aged out of the
            # current lookback window (7-day cache expired, re-run after weeks, etc).
            # Ensures the playlist always reflects the full pending acquisition state.
            if run_mode == "fetch":
                queued_items = (
                    rythmx_store.get_queue(status="pending") +
                    rythmx_store.get_queue(status="submitted")
                )
                in_playlist = {
                    (music_client.norm(t["artist_name"]), music_client.norm(t["track_name"]))
                    for t in playlist_tracks if not t.get("is_owned")
                }
                for q in queued_items:
                    key = (music_client.norm(q["artist_name"]), music_client.norm(q["album_title"]))
                    if key not in in_playlist:
                        playlist_tracks.append({
                            "plex_rating_key": None,
                            "track_name": q["album_title"],
                            "artist_name": q["artist_name"],
                            "album_name": q["album_title"],
                            "album_cover_url": "",
                            "score": None,
                            "is_owned": 0,
                            "release_date": q.get("release_date") or "",
                        })
                        in_playlist.add(key)
                        logger.debug(
                            "Stage 7: seeded queued release '%s — %s' from download_queue",
                            q["artist_name"], q["album_title"],
                        )

            # Cap owned tracks at max_playlist_tracks (unowned album cards are kept)
            max_pl = int(settings.get("max_playlist_tracks", 50))
            owned_tracks  = [t for t in playlist_tracks if t.get("is_owned")]
            unowned_cards = [t for t in playlist_tracks if not t.get("is_owned")]
            if len(owned_tracks) > max_pl:
                logger.info("Stage 7: capping owned tracks at %d (had %d)", max_pl, len(owned_tracks))
                owned_tracks = owned_tracks[:max_pl]
            playlist_tracks = owned_tracks + unowned_cards

            owned_track_count = len(owned_tracks)
            unowned_count = len(unowned_cards)
            rythmx_store.create_playlist_meta(playlist_name_date, source="new_music", mode="new_music")
            rythmx_store.save_playlist(playlist_tracks, playlist_name=playlist_name_date)
            rythmx_store.mark_playlist_synced(playlist_name_date)
            logger.info("Stage 7: playlist '%s' saved — %d owned tracks, %d missing albums",
                        playlist_name_date, owned_track_count, unowned_count)

            if auto_push and playlist_tracks:
                # Plex push: only owned tracks with a valid plex_rating_key
                rating_keys = [t["plex_rating_key"] for t in playlist_tracks
                               if t.get("is_owned", 1) and t.get("plex_rating_key")]
                if rating_keys:
                    plex_playlist_id = plex_push.create_or_update_playlist(
                        playlist_name_date, rating_keys)
                    if plex_playlist_id:
                        rythmx_store.update_playlist_plex_id(playlist_name_date, plex_playlist_id)

        except Exception as e:
            logger.warning("Stage 7 playlist build failed (non-fatal): %s", e)
    else:
        logger.info("Stage 7: skipped (run_mode=preview)")

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
    """
    Rebuild a single auto_sync playlist in-place.

    Dispatches by source:
      cc     — re-expand owned_releases to tracks using current library state
      taste  — rebuild using latest Last.fm top artists + current library
      deezer / spotify / lastfm — re-import from source_url
    """
    from app.clients import last_fm_client
    from app.services import engine

    name = pl.get("name") or pl.get("playlist_name")
    source = pl.get("source", "")

    try:
        if source == "new_music":
            # Re-expand owned releases to tracks (same logic as Stage 7, but in-place)
            playlist_tracks = []
            for r in owned_releases:
                cached_r = rythmx_store.get_cached_artist(r.artist) or {}
                ss_id = (cached_r.get("soulsync_artist_id")
                         or library_reader.get_native_artist_id(r.artist))
                if ss_id:
                    tracks = library_reader.get_tracks_for_album(ss_id, r.title)
                    for t in tracks:
                        playlist_tracks.append({
                            "plex_rating_key": t["plex_rating_key"],
                            "track_name": t["track_title"],
                            "artist_name": r.artist,
                            "album_name": r.title,
                            "album_cover_url": t.get("album_thumb_url") or "",
                            "score": None,
                        })
            rythmx_store.save_playlist(playlist_tracks, playlist_name=name)
            rythmx_store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced new_music playlist '%s' (%d tracks)", name, len(playlist_tracks))

        elif source == "taste":
            # Rebuild taste playlist using top_artists already fetched in Stage 1
            meta = rythmx_store.get_playlist_meta(name) or {}
            max_tracks = int(meta.get("max_tracks") or 50)
            max_per_artist = int(meta.get("max_per_artist") or 2)
            loved = last_fm_client.get_loved_artist_names()

            artist_tracks = {}
            for artist_name in top_artists:
                cached = rythmx_store.get_cached_artist(artist_name) or {}
                ss_id = cached.get("soulsync_artist_id") or library_reader.get_native_artist_id(artist_name)
                if ss_id:
                    tracks = library_reader.get_all_tracks_for_artist(ss_id)
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
            rythmx_store.save_playlist(to_save, playlist_name=name)
            rythmx_store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced taste playlist '%s' (%d tracks)", name, len(to_save))

        elif source in ("spotify", "lastfm", "deezer"):
            from app.services import playlist_importer
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
    Checks app_settings['release_cache_last_cleared_weekday'] to avoid multiple
    refreshes in the same day.
    """
    weekday = int(settings.get("release_cache_refresh_weekday", "3"))
    hour = int(settings.get("release_cache_refresh_hour", "5"))
    now = datetime.utcnow()
    if now.weekday() != weekday or now.hour < hour:
        return False
    last_cleared = rythmx_store.get_setting("release_cache_last_cleared_weekday") or ""
    return last_cleared != now.date().isoformat()


def _should_run_cc(settings: dict) -> bool:
    """
    Return True if it's time to run a CC cycle.
    If schedule_weekday and schedule_hour are both set (≥ 0), use day/time scheduling.
    Otherwise falls back to cycle_hours interval.
    """
    now = datetime.now()
    weekday = int(settings.get("schedule_weekday") or -1)
    hour = int(settings.get("schedule_hour") or -1)
    last_run_iso = settings.get("last_run")

    if weekday >= 0 and hour >= 0:
        # Day/time mode: run if it's the right weekday and hour
        if now.weekday() != weekday or now.hour != hour:
            return False
        # Avoid running more than once in the same hour
        if last_run_iso:
            last = datetime.fromisoformat(last_run_iso)
            if last.date() == now.date() and last.hour == now.hour:
                return False
        return True

    # Interval mode (default)
    cycle_hours = int(settings.get("cycle_hours") or config.CYCLE_HOURS)
    if not last_run_iso:
        return True
    last = datetime.fromisoformat(last_run_iso)
    return (now - last).total_seconds() >= cycle_hours * 3600


def _should_library_sync(settings: dict) -> bool:
    """
    Return True if it's time to run the library auto-pipeline.
    Checks: connections verified, lib_auto_sync enabled, interval elapsed, not already running.
    """
    from app.services.enrichment.runner import PipelineRunner
    if PipelineRunner.is_running():
        return False
    # Green light: library reader must be verified
    if not settings.get("plex_verified_at"):
        return False
    auto_sync = settings.get("lib_auto_sync")
    if auto_sync is not None and str(auto_sync).lower() in ("false", "0", "no"):
        return False
    last_synced = settings.get("library_last_synced")
    if not last_synced:
        return True  # never synced
    try:
        interval_hours = int(settings.get("lib_sync_interval_hours", 24))
        last = datetime.fromisoformat(last_synced)
        return (datetime.utcnow() - last).total_seconds() >= interval_hours * 3600
    except (TypeError, ValueError):
        return True


def _loop():
    """Background loop — checks every hour whether a CC cycle should run."""
    while not _stop_event.is_set():
        ran_cc = False
        if config.SCHEDULER_ENABLED:
            settings = rythmx_store.get_all_settings()
            if _should_run_cc(settings):
                mode = settings.get("run_mode", "fetch")
                force = _should_weekly_refresh(settings)
                if force:
                    rythmx_store.set_setting("release_cache_last_cleared_weekday",
                                         datetime.utcnow().date().isoformat())
                    logger.info("Weekly release cache refresh triggered (weekday=%s, hour=%s)",
                                settings.get("release_cache_refresh_weekday", "3"),
                                settings.get("release_cache_refresh_hour", "5"))
                run_cycle(run_mode=mode, force_refresh=force, triggered_by="schedule")
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
