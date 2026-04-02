"""
Helper logic extracted from scheduler.py to reduce monolith size.
"""
from __future__ import annotations

from datetime import datetime

from app import config


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
      taste - rebuild using latest Last.fm top artists + current library
      deezer / spotify / lastfm - re-import from source_url
    """
    from app.clients import last_fm_client
    from app.services import engine

    name = pl.get("name") or pl.get("playlist_name")
    source = pl.get("source", "")

    try:
        if source == "new_music":
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
            logger.info("Stage 8: auto-synced new_music playlist '%s' (%d tracks)", name, len(playlist_tracks))

        elif source == "taste":
            meta = store.get_playlist_meta(name) or {}
            max_tracks = int(meta.get("max_tracks") or 50)
            max_per_artist = int(meta.get("max_per_artist") or 2)
            loved = last_fm_client.get_loved_artist_names()

            artist_tracks = {}
            for artist_name in top_artists:
                cached = store.get_cached_artist(artist_name) or {}
                ss_id = cached.get("soulsync_artist_id") or library_reader.get_native_artist_id(artist_name)
                if ss_id:
                    tracks = library_reader.get_all_tracks_for_artist(ss_id)
                    if tracks:
                        artist_tracks[artist_name] = tracks

            scored = engine.build_taste_playlist(
                top_artists,
                loved,
                artist_tracks,
                limit=max_tracks,
                max_per_artist=max_per_artist,
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
            store.save_playlist(to_save, playlist_name=name)
            store.mark_playlist_synced(name)
            logger.info("Stage 8: auto-synced taste playlist '%s' (%d tracks)", name, len(to_save))

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

    except Exception as e:
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

    except Exception as e:
        logger.warning("Stage 7 playlist build failed (non-fatal): %s", e)

    return playlist_tracks, plex_playlist_id
