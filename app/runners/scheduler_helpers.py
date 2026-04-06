"""
Helper logic extracted from scheduler.py to reduce monolith size.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from datetime import date as _date

from app import config

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


def _is_forge_new_music_source(source: str | None) -> bool:
    normalized = (source or "").strip().lower()
    return normalized in ("new_music", "forge_new_music")


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


def parse_cycle_settings(settings: dict) -> dict:
    """
    Parse and normalize CC cycle settings from app_settings/config defaults.
    """
    min_listens = int(settings.get("min_listens", config.MIN_LISTENS))
    lookback_days = int(settings.get("lookback_days", config.LOOKBACK_DAYS))
    max_per_cycle = int(settings.get("max_per_cycle", config.MAX_PER_CYCLE))
    period = settings.get("period", "1month")
    auto_push = settings.get("auto_push_playlist", "false") == "true"

    ignore_kw_raw = settings.get("nr_ignore_keywords", "") or config.IGNORE_KEYWORDS
    ignore_keywords = [k.strip() for k in ignore_kw_raw.split(",") if k.strip()]

    strip_punct = lambda s: re.sub(r"[^\w\s]", "", s).strip()
    ignore_artists = {
        strip_punct(a.strip().lower())
        for a in settings.get("nr_ignore_artists", "").split(",")
        if a.strip()
    }

    release_kinds_raw = settings.get("release_kinds") or config.RELEASE_KINDS
    allowed_kinds = {k.strip().lower() for k in release_kinds_raw.split(",") if k.strip()}

    include_features_raw = settings.get("include_features")
    include_features = (
        True if include_features_raw is None else str(include_features_raw).lower() not in ("false", "0", "no")
    )

    return {
        "min_listens": min_listens,
        "lookback_days": lookback_days,
        "max_per_cycle": max_per_cycle,
        "period": period,
        "auto_push": auto_push,
        "ignore_keywords": ignore_keywords,
        "ignore_artists": ignore_artists,
        "allowed_kinds": allowed_kinds,
        "include_features": include_features,
    }


def should_run_cc(settings: dict) -> bool:
    """
    Return True if it's time to run a CC cycle.
    If schedule_weekday and schedule_hour are both set (>= 0), use day/time scheduling.
    Otherwise fall back to cycle_hours interval.
    """
    now = datetime.now()
    weekday = int(settings.get("schedule_weekday") or -1)
    hour = int(settings.get("schedule_hour") or -1)
    last_run_iso = settings.get("last_run")

    if weekday >= 0 and hour >= 0:
        if now.weekday() != weekday or now.hour != hour:
            return False
        if last_run_iso:
            last = datetime.fromisoformat(last_run_iso)
            if last.date() == now.date() and last.hour == now.hour:
                return False
        return True

    cycle_hours = int(settings.get("cycle_hours") or config.CYCLE_HOURS)
    if not last_run_iso:
        return True
    last = datetime.fromisoformat(last_run_iso)
    return (now - last).total_seconds() >= cycle_hours * 3600


def should_library_sync(settings: dict) -> bool:
    """
    Return True if it's time to run the library auto-pipeline.
    Checks: connections verified, lib_auto_sync enabled, interval elapsed, not already running.
    """
    from app.services.enrichment.runner import PipelineRunner

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


def run_scheduler_tick(settings: dict, run_cycle_fn, store, logger) -> bool:
    """
    Execute one scheduler tick decision:
    - run CC if due
    - trigger library auto-pipeline if due
    Returns whether a CC cycle was run.
    """
    ran_cc = False
    if should_run_cc(settings):
        mode = settings.get("run_mode", "fetch")
        run_cycle_fn(run_mode=mode, force_refresh=False, triggered_by="schedule")
        store.set_setting("last_run", datetime.now().isoformat())
        ran_cc = True

    if should_library_sync(settings):
        try:
            from app.services.enrichment.runner import PipelineRunner

            threading.Thread(
                target=PipelineRunner().run,
                kwargs={"on_progress": None},
                daemon=True,
                name="lib-pipeline",
            ).start()
            logger.info("Library auto-pipeline triggered by scheduler")
        except NON_FATAL_SCHEDULER_ERRORS as e:
            logger.warning("Library auto-pipeline launch failed: %s", e)
    return ran_cc


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


def auto_sync_playlist(
    pl,
    owned_releases,
    top_artists,
    settings,
    library_reader,
    store,
    logger,
):
    """
    Rebuild a single auto_sync playlist in-place.

    Dispatches by source:
      new_music - re-expand owned_releases to tracks using current library state
      deezer / spotify / lastfm - re-import from source_url
    """

    name = pl.get("name") or pl.get("playlist_name")
    source = pl.get("source", "")

    try:
        if _is_forge_new_music_source(source):
            playlist_tracks = []
            for r in owned_releases:
                cached_r = store.get_cached_artist(r.artist) or {}
                ss_id = cached_r.get("soulsync_artist_id") or library_reader.get_native_artist_id(r.artist)
                if ss_id:
                    tracks = library_reader.get_tracks_for_album(ss_id, r.title)
                    for t in tracks:
                        playlist_tracks.append(
                            {
                                "plex_rating_key": t["plex_rating_key"],
                                "track_name": t["track_title"],
                                "artist_name": r.artist,
                                "album_name": r.title,
                                "album_cover_url": t.get("album_thumb_url") or "",
                                "score": None,
                            }
                        )
            store.save_playlist(playlist_tracks, playlist_name=name)
            store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced Forge playlist '%s' (%d tracks)", name, len(playlist_tracks))

        elif source == "taste":
            logger.info(
                "Stage 8: skipping legacy taste playlist '%s' (autosync path retired)",
                name,
            )

        elif source in ("spotify", "lastfm", "deezer"):
            from app.services import playlist_importer

            source_url = pl.get("source_url") or ""
            if not source_url:
                logger.warning("Stage 8: skipping '%s' - no source_url stored", name)
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

    except NON_FATAL_SCHEDULER_ERRORS as e:
        logger.warning("Stage 8: auto-sync failed for playlist '%s': %s", name, e)


def build_named_playlist(
    run_mode: str,
    owned_releases,
    unowned,
    settings: dict,
    library_reader,
    store,
    music_client,
    plex_push,
    playlist_name_date: str | None,
    auto_push: bool,
    logger,
):
    """
    Stage 7 playlist builder:
    - expand owned releases to tracks
    - add unowned album cards
    - seed pending/submitted queue rows for fetch mode
    - cap owned track count
    - save named playlist and optional Plex push
    """
    playlist_tracks = []
    plex_playlist_id = None

    if run_mode not in ("build", "fetch"):
        logger.info("Stage 7: skipped (run_mode=preview)")
        return playlist_tracks, plex_playlist_id

    try:
        for r in owned_releases:
            cached_r = store.get_cached_artist(r.artist) or {}
            ss_id = cached_r.get("soulsync_artist_id") or library_reader.get_native_artist_id(r.artist)
            if ss_id:
                tracks = library_reader.get_tracks_for_album(ss_id, r.title)
                for t in tracks:
                    playlist_tracks.append(
                        {
                            "plex_rating_key": t["plex_rating_key"],
                            "track_name": t["track_title"],
                            "artist_name": r.artist,
                            "album_name": r.title,
                            "album_cover_url": t.get("album_thumb_url") or "",
                            "score": None,
                            "is_owned": 1,
                            "release_date": r.release_date,
                        }
                    )
            else:
                logger.debug("Stage 7: no native artist ID for owned release artist '%s'", r.artist)

        for r in unowned:
            playlist_tracks.append(
                {
                    "plex_rating_key": None,
                    "track_name": r.title,
                    "artist_name": r.artist,
                    "album_name": r.title,
                    "album_cover_url": "",
                    "score": None,
                    "is_owned": 0,
                    "release_date": r.release_date,
                }
            )

        if run_mode == "fetch":
            queued_items = store.get_queue(status="pending") + store.get_queue(status="submitted")
            in_playlist = {
                (music_client.norm(t["artist_name"]), music_client.norm(t["track_name"]))
                for t in playlist_tracks
                if not t.get("is_owned")
            }
            for q in queued_items:
                key = (music_client.norm(q["artist_name"]), music_client.norm(q["album_title"]))
                if key not in in_playlist:
                    playlist_tracks.append(
                        {
                            "plex_rating_key": None,
                            "track_name": q["album_title"],
                            "artist_name": q["artist_name"],
                            "album_name": q["album_title"],
                            "album_cover_url": "",
                            "score": None,
                            "is_owned": 0,
                            "release_date": q.get("release_date") or "",
                        }
                    )
                    in_playlist.add(key)
                    logger.debug(
                        "Stage 7: seeded queued release '%s - %s' from download_queue",
                        q["artist_name"],
                        q["album_title"],
                    )

        max_pl = int(settings.get("max_playlist_tracks", 50))
        owned_tracks = [t for t in playlist_tracks if t.get("is_owned")]
        unowned_cards = [t for t in playlist_tracks if not t.get("is_owned")]
        if len(owned_tracks) > max_pl:
            logger.info("Stage 7: capping owned tracks at %d (had %d)", max_pl, len(owned_tracks))
            owned_tracks = owned_tracks[:max_pl]
        playlist_tracks = owned_tracks + unowned_cards

        owned_track_count = len(owned_tracks)
        unowned_count = len(unowned_cards)
        store.create_playlist_meta(playlist_name_date, source="new_music", mode="new_music")
        store.save_playlist(playlist_tracks, playlist_name=playlist_name_date)
        store.mark_playlist_synced(playlist_name_date)
        logger.info(
            "Stage 7: playlist '%s' saved - %d owned tracks, %d missing albums",
            playlist_name_date,
            owned_track_count,
            unowned_count,
        )

        if auto_push and playlist_tracks:
            rating_keys = [
                t["plex_rating_key"]
                for t in playlist_tracks
                if t.get("is_owned", 1) and t.get("plex_rating_key")
            ]
            if rating_keys:
                plex_playlist_id = plex_push.create_or_update_playlist(playlist_name_date, rating_keys)
                if plex_playlist_id:
                    store.update_playlist_plex_id(playlist_name_date, plex_playlist_id)

    except NON_FATAL_SCHEDULER_ERRORS as e:
        logger.warning("Stage 7 playlist build failed (non-fatal): %s", e)

    return playlist_tracks, plex_playlist_id


def run_stage8_autosync(
    run_mode: str,
    owned_releases,
    top_artists,
    settings: dict,
    library_reader,
    store,
    logger,
):
    """
    Stage 8 orchestration:
    - rebuild all auto_sync playlists when run_mode is build/fetch
    - skip in preview mode
    """
    if run_mode in ("build", "fetch"):
        auto_playlists = [p for p in store.list_playlists() if p.get("auto_sync")]
        logger.info("Stage 8: %d auto-sync playlist(s) to rebuild", len(auto_playlists))
        for pl in auto_playlists:
            auto_sync_playlist(pl, owned_releases, top_artists, settings, library_reader, store, logger)
    else:
        logger.info("Stage 8: skipped (run_mode=preview)")


def write_cycle_history(
    run_mode: str,
    to_queue,
    owned_releases,
    unowned,
    store,
    logger,
):
    """
    Persist cycle history entries.
    Skips writes in preview mode and treats failures as non-fatal.
    """
    if run_mode == "preview":
        return

    try:
        queued_keys = {(r.artist, r.title) for r in to_queue}
        for r in owned_releases:
            store.add_history_entry({"artist_name": r.artist, "album_name": r.title}, status="owned")

        for r in unowned:
            if (r.artist, r.title) in queued_keys:
                entry_status, entry_reason = "queued", ""
            elif run_mode == "fetch" and store.is_in_queue(r.artist, r.title):
                entry_status, entry_reason = "queued", "already_queued"
            else:
                entry_status = "skipped"
                entry_reason = "build_mode" if run_mode == "build" else ""
            store.add_history_entry(
                {"artist_name": r.artist, "album_name": r.title},
                status=entry_status,
                reason=entry_reason,
            )
    except NON_FATAL_SCHEDULER_ERRORS as e:
        logger.warning("History write failed (non-fatal): %s", e)


def classify_owned_releases(
    unique_releases,
    library_reader,
    store,
    logger,
):
    """
    Stage 4 classifier:
    split releases into owned/unowned using library_reader owned-check.
    """
    owned_releases = []
    unowned = []
    owned_count = 0

    for r in unique_releases:
        cached_r = store.get_cached_artist(r.artist) or {}
        ss_id = cached_r.get("soulsync_artist_id") or library_reader.get_native_artist_id(r.artist)
        sp_id = library_reader.get_spotify_artist_id(r.artist)
        it_id = cached_r.get("itunes_artist_id") or library_reader.get_itunes_artist_id(r.artist)
        rating_key = library_reader.check_album_owned(
            r.artist,
            r.title,
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
    return owned_releases, unowned, owned_count


def seed_release_artwork_cache(owned_releases, unowned, store) -> None:
    """
    Seed image cache with release artwork URLs discovered in provider lookups.
    """
    for r in owned_releases + unowned:
        if r.artwork_url:
            img_key = f"{r.artist.lower()}|||{r.title.lower()}"
            store.set_image_cache("album", img_key, r.artwork_url)


def queue_unowned_releases(
    run_mode: str,
    unowned,
    max_per_cycle: int,
    playlist_name_date: str | None,
    store,
    logger,
):
    """
    Stage 5-6 acquisition queue orchestration.
    Returns (queued_count, to_queue).
    """
    queued_count = 0
    to_queue = []

    if run_mode != "fetch":
        logger.info("Stage 5-6: skipped (not fetch mode, run_mode=%s)", run_mode)
        return queued_count, to_queue

    unowned.sort(key=lambda r: r.release_date, reverse=True)
    today_str = _date.today().isoformat()
    new_unowned = [
        r for r in unowned if not store.is_in_queue(r.artist, r.title) and (r.release_date or "9999") <= today_str
    ]
    skipped_count = len(unowned) - len(new_unowned)
    if skipped_count:
        logger.info("Stage 5: skipped %d releases already in acquisition queue", skipped_count)
    to_queue = new_unowned[:max_per_cycle]
    logger.info("Stage 5: %d releases selected for acquisition (cap=%d)", len(to_queue), max_per_cycle)

    for r in to_queue:
        queue_id = store.add_to_queue(
            artist_name=r.artist,
            album_title=r.title,
            release_date=r.release_date,
            kind=r.kind,
            source=r.source,
            itunes_album_id=r.itunes_album_id or None,
            deezer_album_id=r.deezer_album_id or None,
            spotify_album_id=r.spotify_album_id or None,
            requested_by="cc",
            playlist_name=playlist_name_date,
        )
        queued_count += 1
        logger.info("Stage 6: queued '%s - %s' (queue_id=%d)", r.artist, r.title, queue_id)

    logger.info("Stage 6: %d releases added to acquisition queue", queued_count)
    return queued_count, to_queue


def discover_releases_for_qualified_artists(
    qualified: dict,
    lookback_days: int,
    ignore_keywords: list[str],
    allowed_kinds: set[str],
    force_refresh: bool,
    library_reader,
    store,
    identity_resolver,
    music_client,
    ignore_artists: set[str],
    include_features: bool,
    logger,
):
    """
    Stage 2-3 discovery:
    - resolve identities and gather releases for qualified artists
    - dedupe by normalized artist/title
    - apply ignore_artists / ignore_keywords / include_features filters
    """
    strip_punct = lambda s: re.sub(r"[^\w\s]", "", s).strip()

    all_releases = []
    artists_with_releases = 0

    for artist_name in qualified:
        cached = store.get_cached_artist(artist_name) or {}

        identity = identity_resolver.resolve_artist(artist_name)
        identity_itunes_id = identity.get("itunes_artist_id")
        if identity_itunes_id and not cached.get("itunes_artist_id"):
            cached["itunes_artist_id"] = identity_itunes_id
        logger.debug(
            "Identity: %s -> iTunes:%s (confidence=%d, method=%s)",
            artist_name,
            identity_itunes_id or "none",
            identity.get("confidence", 0),
            (identity.get("reason_codes") or ["?"])[-1],
        )

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

        if resolved_ids or ss_artist_id:
            store.cache_artist(
                lastfm_name=artist_name,
                deezer_artist_id=resolved_ids.get("deezer_artist_id"),
                spotify_artist_id=resolved_ids.get("spotify_artist_id"),
                itunes_artist_id=resolved_ids.get("itunes_artist_id"),
                mb_artist_id=resolved_ids.get("mb_artist_id"),
                soulsync_artist_id=ss_artist_id,
                confidence=identity.get("confidence", 90),
            )

        if releases:
            for r in releases:
                if not r.artist:
                    r.artist = artist_name
            all_releases.extend(releases)
            artists_with_releases += 1

    seen = set()
    unique_releases = []
    for r in all_releases:
        if ignore_artists and strip_punct(r.artist.lower()) in ignore_artists:
            logger.debug("Ignoring artist: %s", r.artist)
            continue
        if ignore_keywords and any(kw in r.title.lower() for kw in ignore_keywords):
            logger.debug("Ignoring release (keyword match): %s - %s", r.artist, r.title)
            continue
        key = (music_client.norm(r.artist), music_client.norm(r.title))
        if key not in seen:
            seen.add(key)
            unique_releases.append(r)

    if not include_features:
        feat_re = re.compile(r"\b(feat\.?|ft\.?|featuring)\b|\(with ", re.IGNORECASE)
        before = len(unique_releases)
        unique_releases = [r for r in unique_releases if not feat_re.search(r.title)]
        filtered = before - len(unique_releases)
        if filtered:
            logger.info(
                "Stage 2-3: filtered %d feature/collab release(s) (include_features=false)",
                filtered,
            )

    return unique_releases, artists_with_releases
