"""
rythmx_store.py â€” rythmx's own SQLite database (rythmx.db).

All tables owned by rythmx. Never touches SoulSync's DB.
All queries use parameterized form only.
"""
import sqlite3
import logging
from app import config
from app.db.store import api_keys as _api_keys_store
from app.db.store import download_jobs as _download_jobs_store
from app.db.store import download_queue as _download_queue_store
from app.db.store import forge_builds as _forge_builds_store
from app.db.store import forge_playlists as _forge_playlists_store
from app.db.store import history as _history_store
from app.db.store import image_cache as _image_cache_store
from app.db.store import playlist as _playlist_store
from app.db.store import artist_identity as _artist_identity_store
from app.db.store import release_maintenance as _release_maintenance_store
from app.db.store import settings as _settings_store
from app.db.store import taste_cache as _taste_cache_store
from app.db.store import pipeline_history as _pipeline_history_store

logger = logging.getLogger(__name__)


def _connect():
    conn = sqlite3.connect(config.RYTHMX_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn



_TRIGGER_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_lib_releases_artistid_insert
BEFORE INSERT ON lib_releases
WHEN NEW.artist_id IS NULL
BEGIN
    INSERT INTO fk_violation_log(table_name, op, row_id, payload)
    VALUES ('lib_releases', 'INSERT_NULL_ARTIST', NEW.id,
            json_object('id', NEW.id, 'artist_name', NEW.artist_name));
    SELECT RAISE(ABORT, 'lib_releases.artist_id must not be NULL');
END;

CREATE TRIGGER IF NOT EXISTS trg_lib_releases_artistid_update
BEFORE UPDATE ON lib_releases
WHEN NEW.artist_id IS NULL
BEGIN
    INSERT INTO fk_violation_log(table_name, op, row_id, payload)
    VALUES ('lib_releases', 'UPDATE_NULL_ARTIST', NEW.id,
            json_object('id', NEW.id, 'artist_name', NEW.artist_name));
    SELECT RAISE(ABORT, 'lib_releases.artist_id must not be NULL on UPDATE');
END;
"""


def init_db():
    """Create all rythmx.db tables if they don't exist. Safe to call on every startup.

    The genesis migration (000_genesis.sql) creates ALL tables and indexes.
    init_db() only runs migrations and performs post-schema maintenance.
    """
    from migrations.runner import run_pending_migrations
    run_pending_migrations(config.RYTHMX_DB)

    with _connect() as conn:
        # Apply enforcement triggers via executescript() â€” the migration runner
        # cannot handle multi-statement DDL (splits on semicolons).
        conn.executescript(_TRIGGER_DDL)
        # Prune stale image cache entries (> 30 days since last access)
        conn.execute("DELETE FROM image_cache WHERE last_accessed < datetime('now', '-30 days')")

    logger.info("rythmx.db initialized at %s", config.RYTHMX_DB)

    # After schema is ready, clean up secondary catalog rows if single-catalog mode
    ensure_single_catalog_cleanup()


# --- API Key ---

def get_api_key() -> str | None:
    return _api_keys_store.get_api_key(_connect)

def _set_api_key(key: str) -> None:
    _api_keys_store.set_api_key(_connect, key)

def generate_new_api_key() -> str:
    return _api_keys_store.generate_new_api_key(_connect)

# --- Image Cache ---

def get_image_cache(entity_type: str, entity_key: str) -> str | None:
    return _image_cache_store.get_image_cache(_connect, entity_type, entity_key)


def set_image_cache(entity_type: str, entity_key: str, image_url: str):
    _image_cache_store.set_image_cache(_connect, entity_type, entity_key, image_url)


def set_image_cache_entry(
    entity_type: str,
    entity_key: str,
    image_url: str,
    local_path: str | None = None,
    content_hash: str | None = None,
    artwork_source: str | None = None,
):
    _image_cache_store.set_image_cache_entry(
        _connect,
        entity_type,
        entity_key,
        image_url,
        local_path,
        content_hash,
        artwork_source,
    )


def get_image_cache_entry(entity_type: str, entity_key: str) -> dict | None:
    return _image_cache_store.get_image_cache_entry(_connect, entity_type, entity_key)


def clear_image_cache():
    _image_cache_store.clear_image_cache(_connect)


# --- Settings ---

def get_setting(key: str, default=None):
    return _settings_store.get_setting(_connect, key, default)


def set_setting(key: str, value: str):
    _settings_store.set_setting(_connect, key, value)


def get_all_settings() -> dict:
    return _settings_store.get_all_settings(_connect)


# --- History ---

def add_history_entry(track: dict, status: str, reason: str = ""):
    _history_store.add_history_entry(_connect, track, status, reason)


def get_history(limit: int = 100) -> list[dict]:
    return _history_store.get_history(_connect, limit)


def is_release_in_history(artist_name: str, album_name: str) -> bool:
    return _history_store.is_release_in_history(_connect, artist_name, album_name)


# --- Download queue ---

def is_in_queue(artist_name: str, album_title: str) -> bool:
    return _download_queue_store.is_in_queue(_connect, artist_name, album_title)


def add_to_queue(artist_name: str, album_title: str, release_date: str = None,
                 kind: str = None, source: str = None,
                 itunes_album_id: str = None, deezer_album_id: str = None,
                 spotify_album_id: str = None,
                 requested_by: str = "cc", playlist_name: str = None) -> int:
    return _download_queue_store.add_to_queue(
        _connect,
        artist_name,
        album_title,
        release_date,
        kind,
        source,
        itunes_album_id,
        deezer_album_id,
        spotify_album_id,
        requested_by,
        playlist_name,
    )


def get_queue(status: str = None, playlist_name: str = None) -> list[dict]:
    return _download_queue_store.get_queue(_connect, status, playlist_name)


def update_queue_status(queue_id: int, status: str, provider_response: str = None):
    _download_queue_store.update_queue_status(_connect, queue_id, status, provider_response)


def get_queue_stats() -> dict:
    return _download_queue_store.get_queue_stats(_connect)


def get_queue_status(artist_name: str, album_title: str) -> str | None:
    return _download_queue_store.get_queue_status(_connect, artist_name, album_title)


def get_history_summary() -> dict:
    return _history_store.get_history_summary(_connect)


# --- Playlist ---

def save_playlist(tracks: list[dict], playlist_name: str = "For You"):
    _playlist_store.save_playlist(_connect, tracks, playlist_name)


def get_playlist(playlist_name: str = "For You") -> list[dict]:
    return _playlist_store.get_playlist(_connect, playlist_name)


def add_to_playlist(track: dict, playlist_name: str = "For You"):
    _playlist_store.add_to_playlist(_connect, track, playlist_name)


def remove_from_playlist(track_id: str, playlist_name: str = "For You"):
    _playlist_store.remove_from_playlist(_connect, track_id, playlist_name)


def update_playlist_plex_id(playlist_name: str, plex_playlist_id: str):
    _playlist_store.update_playlist_plex_id(_connect, playlist_name, plex_playlist_id)


# --- Artist identity cache ---

def get_lib_artist_ids(artist_name: str) -> dict | None:
    return _artist_identity_store.get_lib_artist_ids(_connect, artist_name)


def get_cached_artist(lastfm_name: str) -> dict | None:
    return _artist_identity_store.get_cached_artist(_connect, lastfm_name)


def cache_artist(lastfm_name: str, deezer_artist_id: str = None,
                 spotify_artist_id: str = None, itunes_artist_id: str = None,
                 mb_artist_id: str = None, soulsync_artist_id: str = None,
                 confidence: int = 80, resolution_method: str = None):
    _artist_identity_store.cache_artist(
        _connect,
        lastfm_name,
        deezer_artist_id,
        spotify_artist_id,
        itunes_artist_id,
        mb_artist_id,
        soulsync_artist_id,
        confidence,
        resolution_method,
    )


def get_artist_navidrome_cover(artist_name: str) -> str | None:
    return _artist_identity_store.get_artist_navidrome_cover(_connect, artist_name)


# --- Taste cache ---

def upsert_taste_cache(artist_name: str, play_count: int, period: str):
    _taste_cache_store.upsert_taste_cache(_connect, artist_name, play_count, period)


def get_taste_cache() -> dict:
    return _taste_cache_store.get_taste_cache(_connect)


# --- Maintenance ---

def clear_history():
    _history_store.clear_history(_connect)


def reset_db():
    """Wipe all user data tables. Schema is preserved (re-created by init_db on next start)."""
    with _connect() as conn:
        conn.executescript("""
            DELETE FROM history;
            DELETE FROM playlist_tracks;
            DELETE FROM taste_cache;
            DELETE FROM app_settings;
            DELETE FROM artist_identity_cache;
            DELETE FROM candidates;
            DELETE FROM playlists;
            DELETE FROM download_queue;
        """)
    logger.info("rythmx.db reset â€” all user data cleared")


# --- Playlist metadata (multi-playlist management) ---

def create_playlist_meta(name: str, source: str = "manual", source_url: str = None,
                         auto_sync: bool = False, mode: str = "library_only",
                         max_tracks: int = 50):
    _playlist_store.create_playlist_meta(
        _connect, name, source, source_url, auto_sync, mode, max_tracks
    )


def get_playlist_meta(name: str) -> dict | None:
    return _playlist_store.get_playlist_meta(_connect, name)


def update_playlist_meta(name: str, auto_sync: bool = None, mode: str = None,
                         source_url: str = None, max_tracks: int = None):
    _playlist_store.update_playlist_meta(
        _connect, name, auto_sync, mode, source_url, max_tracks
    )


def mark_playlist_synced(name: str):
    _playlist_store.mark_playlist_synced(_connect, name)


def list_playlists() -> list[dict]:
    return _playlist_store.list_playlists(_connect)


def delete_playlist(name: str):
    _playlist_store.delete_playlist(_connect, name)


def rename_playlist(old_name: str, new_name: str):
    _playlist_store.rename_playlist(_connect, old_name, new_name)


def remove_playlist_row(row_id: int):
    _playlist_store.remove_playlist_row(_connect, row_id)


def get_release_itunes_album_id(artist_name: str, album_title: str) -> str | None:
    return _image_cache_store.get_release_itunes_album_id(_connect, artist_name, album_title)

def get_missing_image_entities(limit: int = 40) -> list[tuple[str, str, str]]:
    return _image_cache_store.get_missing_image_entities(_connect, limit)


def get_artist_artwork_source_counts() -> list[dict]:
    return _image_cache_store.get_artist_artwork_source_counts(_connect)

def backfill_normalized_titles() -> int:
    return _release_maintenance_store.backfill_normalized_titles(_connect, logger)


def recompute_normalized_titles(artist_ids: list[str] | None = None) -> int:
    return _release_maintenance_store.recompute_normalized_titles(_connect, logger, artist_ids)


def refresh_missing_counts(artist_id: str | None = None,
                           artist_ids: list[str] | None = None) -> int:
    return _release_maintenance_store.refresh_missing_counts(
        _connect, logger, artist_id, artist_ids
    )


def populate_canonical_release_ids(artist_id: str | None = None,
                                   artist_ids: list[str] | None = None) -> int:
    return _release_maintenance_store.populate_canonical_release_ids(
        _connect, logger, artist_id, artist_ids
    )


def ensure_single_catalog_cleanup():
    _release_maintenance_store.ensure_single_catalog_cleanup(
        _connect, logger, config.CATALOG_PRIMARY
    )


# ---------------------------------------------------------------------------
# Pipeline history
# ---------------------------------------------------------------------------

def insert_pipeline_run(
    pipeline_type: str,
    run_mode: str,
    config_snapshot: dict,
    triggered_by: str = "manual",
) -> int:
    return _pipeline_history_store.insert_pipeline_run(
        _connect, pipeline_type, run_mode, config_snapshot, triggered_by
    )


def complete_pipeline_run(
    run_id: int,
    summary: dict,
    error_message: str | None = None,
) -> None:
    _pipeline_history_store.complete_pipeline_run(_connect, run_id, summary, error_message)


def get_pipeline_runs(
    pipeline_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    return _pipeline_history_store.get_pipeline_runs(_connect, pipeline_type, limit)


# ---------------------------------------------------------------------------
# Forge builds
# ---------------------------------------------------------------------------

def create_forge_build(
    name: str,
    source: str = "manual",
    status: str = "ready",
    track_list: list[dict] | list | None = None,
    summary: dict | None = None,
    run_mode: str | None = None,
    build_id: str | None = None,
) -> dict:
    return _forge_builds_store.create_forge_build(
        _connect,
        name=name,
        source=source,
        status=status,
        track_list=track_list,
        summary=summary,
        run_mode=run_mode,
        build_id=build_id,
    )


# --- Plugin slot config ---

def get_all_plugin_slot_config() -> dict[tuple[str, str], bool]:
    """Returns {(plugin_name, slot): enabled} from the plugin_slots table."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT plugin_name, slot, enabled FROM plugin_slots"
        ).fetchall()
    return {(row["plugin_name"], row["slot"]): bool(row["enabled"]) for row in rows}


def set_plugin_slot_enabled(plugin_name: str, slot: str, enabled: bool) -> None:
    """Upsert a plugin slot enabled/disabled state."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO plugin_slots (plugin_name, slot, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(plugin_name, slot) DO UPDATE SET enabled = excluded.enabled
            """,
            (plugin_name, slot, int(enabled)),
        )


def get_all_plugin_settings() -> dict[str, dict[str, str]]:
    """
    Returns plugin config values stored in app_settings.
    Key pattern: plugin.{name}.{config_key}
    Returns: {plugin_name: {config_key: value}}
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'plugin.%'",
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        parts = row["key"].split(".", 2)  # ["plugin", name, config_key]
        if len(parts) == 3:
            _, name, config_key = parts
            result.setdefault(name, {})[config_key] = row["value"]
    return result


# --- Download jobs ---

def insert_download_job(
    *,
    build_id: str,
    job_id: str,
    provider: str,
    artist_name: str,
    album_name: str,
) -> int:
    return _download_jobs_store.insert_job(
        _connect,
        build_id=build_id,
        job_id=job_id,
        provider=provider,
        artist_name=artist_name,
        album_name=album_name,
    )


def get_download_jobs_for_build(build_id: str) -> list[dict]:
    return _download_jobs_store.get_jobs_for_build(_connect, build_id)


def get_pending_download_jobs(provider: str | None = None) -> list[dict]:
    return _download_jobs_store.get_pending_jobs(_connect, provider=provider)


def update_download_job_status(
    job_id: str,
    status: str,
    storage_path: str | None = None,
) -> bool:
    return _download_jobs_store.update_job_status(
        _connect, job_id, status, storage_path=storage_path
    )


# --- Forge builds ---

def list_forge_builds(source: str | None = None, limit: int = 100) -> list[dict]:
    return _forge_builds_store.list_forge_builds(_connect, source=source, limit=limit)


def get_forge_build(build_id: str) -> dict | None:
    return _forge_builds_store.get_forge_build(_connect, build_id)


def delete_forge_build(build_id: str) -> bool:
    return _forge_builds_store.delete_forge_build(_connect, build_id)


def update_forge_build_status(build_id: str, status: str) -> bool:
    return _forge_builds_store.update_forge_build_status(_connect, build_id, status)


def update_forge_build(
    build_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    run_mode: str | None = None,
    track_list: list[dict] | list | None = None,
    summary: dict | None = None,
) -> dict | None:
    return _forge_builds_store.update_forge_build(
        _connect,
        build_id,
        name=name,
        status=status,
        run_mode=run_mode,
        track_list=track_list,
        summary=summary,
    )


def upsert_forge_playlist(
    playlist_id: str,
    name: str,
    track_ids: list[str],
    pushed_at: str | None = None,
) -> dict:
    return _forge_playlists_store.upsert_forge_playlist(
        _connect,
        playlist_id=playlist_id,
        name=name,
        track_ids=track_ids,
        pushed_at=pushed_at,
    )

