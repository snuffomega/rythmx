"""
new_music_runner.py â€” New Music pipeline for the Forge.

Discovers recent releases from artists in the user's own listening history.

Pipeline:
  1. get_seed_artists()          â€” top artists from Last.fm or Plex play counts
  2. fetch_releases_for_seeds()  â€” Deezer albums for those seed artists directly
  3. write_discovered()          â€” write to forge_discovered_artists + forge_discovered_releases

No ORM. All SQL uses ? placeholders.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app import config
from app.db import rythmx_store
from app.db.sql_helpers import build_in_clause
from app.clients import music_client

logger = logging.getLogger(__name__)


def _connect():
    return rythmx_store._connect()


def _emit_pipeline_progress(
    *,
    pipeline: str,
    run_id: str,
    stage: str,
    processed: int,
    total: int,
    message: str,
) -> None:
    try:
        from app.routes.ws import broadcast

        broadcast(
            "pipeline_progress",
            {
                "pipeline": pipeline,
                "run_id": run_id,
                "stage": stage,
                "processed": int(processed),
                "total": int(total),
                "message": message,
            },
        )
    except Exception:
        pass


def _emit_pipeline_complete(*, pipeline: str, run_id: str, summary: dict[str, Any]) -> None:
    try:
        from app.routes.ws import broadcast

        broadcast(
            "pipeline_complete",
            {
                "pipeline": pipeline,
                "run_id": run_id,
                "summary": summary,
            },
        )
    except Exception:
        pass


def _emit_pipeline_error(*, pipeline: str, run_id: str, message: str) -> None:
    try:
        from app.routes.ws import broadcast

        broadcast(
            "pipeline_error",
            {
                "pipeline": pipeline,
                "run_id": run_id,
                "message": str(message or "Pipeline failed"),
            },
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

NM_DEFAULTS = {
    "nm_min_scrobbles": 10,
    "nm_period": "1month",
    "nm_lookback_days": 90,
    "nm_match_mode": "loose",
    "nm_ignore_keywords": "",
    "nm_ignore_artists": "",
    "nm_release_kinds": "album_preferred",  # all | album_preferred | album
    "nm_schedule_enabled": False,
    "nm_schedule_weekday": 1,
    "nm_schedule_hour": 8,
}

_NM_INT_KEYS = {"nm_min_scrobbles", "nm_lookback_days", "nm_schedule_weekday", "nm_schedule_hour"}
_NM_BOOL_KEYS = {"nm_schedule_enabled"}
_NM_PERIODS = {"7day", "1month", "3month", "6month", "12month", "overall"}
_NM_MATCH_MODES = {"strict", "loose"}
_NM_RELEASE_KINDS = {"all", "album_preferred", "album"}


def get_config() -> dict:
    """Return nm_* config from app_settings, merged with defaults."""
    raw = rythmx_store.get_all_settings()
    result = {}
    for key, default in NM_DEFAULTS.items():
        val = raw.get(key)
        if val is None:
            result[key] = default
        elif key in _NM_BOOL_KEYS:
            result[key] = str(val).lower() in ("true", "1")
        elif key in _NM_INT_KEYS:
            try:
                result[key] = int(val)
            except (ValueError, TypeError):
                result[key] = default
        else:
            result[key] = val
    return result


def validate_config_updates(updates: dict[str, Any]) -> str | None:
    """Return an error message if updates are invalid, otherwise None."""
    if not isinstance(updates, dict):
        return "Invalid payload; expected object"

    for key, value in updates.items():
        if key not in NM_DEFAULTS:
            return f"Unknown config field: {key}"

        if key in _NM_INT_KEYS:
            try:
                iv = int(value)
            except (TypeError, ValueError):
                return f"{key} must be an integer"
            if key == "nm_min_scrobbles" and iv < 1:
                return "nm_min_scrobbles must be >= 1"
            if key == "nm_lookback_days" and iv < 1:
                return "nm_lookback_days must be >= 1"
            if key == "nm_schedule_weekday" and not (0 <= iv <= 6):
                return "nm_schedule_weekday must be between 0 and 6"
            if key == "nm_schedule_hour" and not (0 <= iv <= 23):
                return "nm_schedule_hour must be between 0 and 23"

        if key == "nm_period" and str(value) not in _NM_PERIODS:
            return f"nm_period must be one of: {', '.join(sorted(_NM_PERIODS))}"
        if key == "nm_match_mode" and str(value) not in _NM_MATCH_MODES:
            return f"nm_match_mode must be one of: {', '.join(sorted(_NM_MATCH_MODES))}"
        if key == "nm_release_kinds" and str(value) not in _NM_RELEASE_KINDS:
            return f"nm_release_kinds must be one of: {', '.join(sorted(_NM_RELEASE_KINDS))}"

    return None


def save_config(updates: dict) -> None:
    """Persist nm_* config keys to app_settings. Ignores unknown keys."""
    allowed = set(NM_DEFAULTS.keys())
    for key, value in updates.items():
        if key in allowed:
            rythmx_store.set_setting(key, str(value))


# ---------------------------------------------------------------------------
# Step 1: Seed artists
# ---------------------------------------------------------------------------

def get_seed_artists(period: str, min_scrobbles: int) -> list[dict]:
    """
    Return seed artists with play counts.
    Priority: Last.fm (if configured) -> Plex play_count.
    Returns: [{name: str, name_lower: str, play_count: int}]
    """
    # Try Last.fm first
    if config.LASTFM_USERNAME and config.LASTFM_API_KEY:
        try:
            from app.clients import last_fm_client
            ranked = last_fm_client.get_top_artists_ranked(period=period, limit=500)
            seeds = [
                {"name": a["name"], "name_lower": a["name"].lower(), "play_count": a["playcount"]}
                for a in ranked
                if a.get("playcount", 0) >= min_scrobbles
            ]
            logger.info("new_music: seed_artists=%d from Last.fm (period=%s, min=%d)", len(seeds), period, min_scrobbles)
            return seeds
        except Exception as exc:
            logger.warning("new_music: Last.fm seed failed (%s), falling back to Plex", exc)

    # Fallback: Plex play_count aggregated by artist
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT a.name, a.name_lower, SUM(t.play_count) AS total_plays
            FROM lib_tracks t
            JOIN lib_artists a ON t.artist_id = a.id
            GROUP BY a.id
            HAVING total_plays >= ?
            ORDER BY total_plays DESC
            LIMIT 500
            """,
            (min_scrobbles,)
        ).fetchall()
    seeds = [{"name": r["name"], "name_lower": r["name_lower"], "play_count": r["total_plays"]} for r in rows]
    logger.info("new_music: seed_artists=%d from Plex play counts (min=%d)", len(seeds), min_scrobbles)
    return seeds


# ---------------------------------------------------------------------------
# Step 2 (EARMARKED â€” Custom Discovery engine)
# ---------------------------------------------------------------------------

def expand_neighbors(seed_names: list[str]) -> list[str]:
    """
    1-hop neighbor expansion via lib_artists.similar_artists_json.
    Returns a deduplicated list of neighbor artist names (lowercase) not in library.

    EARMARKED: This is the core of the future Custom Discovery engine.
    New Music uses seed artists directly â€” this function is NOT called from
    run_new_music_pipeline(). Do not delete or repurpose until Custom Discovery
    is implemented.
    """
    if not seed_names:
        return []

    seed_set = {n.lower() for n in seed_names}

    with _connect() as conn:
        # Get library artist names (to exclude from neighbors)
        lib_rows = conn.execute("SELECT name_lower FROM lib_artists").fetchall()
        lib_set = {r["name_lower"] for r in lib_rows}

        # Get similar_artists_json for seed artists
        similar_rows = conn.execute(
            "SELECT similar_artists_json FROM lib_artists WHERE name_lower IN "
            + build_in_clause(len(seed_names)),
            [n.lower() for n in seed_names]
        ).fetchall()

    neighbors = set()
    for row in similar_rows:
        raw = row["similar_artists_json"]
        if not raw:
            continue
        try:
            names = json.loads(raw)
            for name in names:
                # similar_artists_json stores dicts: {"name": str, "match": float, "source": str}
                if isinstance(name, dict):
                    name = name.get("name") or ""
                if not isinstance(name, str) or not name:
                    continue
                nl = name.lower()
                if nl not in lib_set and nl not in seed_set:
                    neighbors.add(nl)
        except (json.JSONDecodeError, TypeError):
            continue

    logger.info("new_music: expanded %d seeds -> %d neighbor candidates", len(seed_names), len(neighbors))
    return list(neighbors)


# ---------------------------------------------------------------------------
# Step 2: Fetch releases for seed artists
# ---------------------------------------------------------------------------

def _apply_kind_preference(artist_releases: list[dict], kinds_mode: str) -> list[dict]:
    """
    Filter/prefer releases by mode:
      all            â€” return everything
      album_preferred â€” return albums if any exist in window, else return singles/EPs
      album          â€” hard filter: albums only (includes 'compile')
    """
    if kinds_mode == "all":
        return artist_releases
    if kinds_mode == "album":
        return [r for r in artist_releases if r["record_type"] in ("album", "compile")]
    if kinds_mode == "album_preferred":
        albums = [r for r in artist_releases if r["record_type"] in ("album", "compile")]
        return albums if albums else artist_releases
    return artist_releases


def fetch_releases_for_neighbors(
    neighbor_names: list[str],
    lookback_days: int,
    match_mode: str,
    release_kinds: str,
    ignore_keywords: str,
    ignore_artists: str,
    *,
    run_id: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    For each artist name, resolve Deezer ID and fetch recent albums.
    Fast path: checks lib_artists.deezer_artist_id first to avoid redundant API searches.
    release_kinds: mode string â€” 'all' | 'album_preferred' | 'album'
    Returns: (discovered_artists, discovered_releases, keyword_filtered_releases)
    """
    cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # Parse ignore lists
    ignore_kw = [kw.strip().lower() for kw in ignore_keywords.split(",") if kw.strip()]
    ignore_art = {a.strip().lower() for a in ignore_artists.split(",") if a.strip()}

    # Fast path: pre-load stored Deezer IDs from lib_artists to skip redundant searches
    stored_deezer_ids: dict[str, str] = {}
    names_lower = [n.lower() for n in neighbor_names]
    if names_lower:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT name_lower, deezer_artist_id FROM lib_artists WHERE name_lower IN "
                + build_in_clause(len(names_lower))
                + " AND deezer_artist_id IS NOT NULL",
                names_lower,
            ).fetchall()
        stored_deezer_ids = {r["name_lower"]: str(r["deezer_artist_id"]) for r in rows}
        logger.info("new_music: fast-path resolved %d/%d artist IDs from lib_artists", len(stored_deezer_ids), len(names_lower))

    discovered_artists = []
    discovered_releases = []
    keyword_filtered = []
    seen_artist_ids = set()

    # Limit to avoid hammering Deezer API on first run
    names_to_process = neighbor_names[:100]
    total_names = len(names_to_process)

    for idx, name in enumerate(names_to_process, start=1):
        if run_id:
            _emit_pipeline_progress(
                pipeline="new_music",
                run_id=run_id,
                stage="releases",
                processed=idx - 1,
                total=max(total_names, 1),
                message=f"Checking releases for {name}",
            )

        if name.lower() in ignore_art:
            continue

        # Resolve Deezer ID â€” fast path first, then API search fallback
        stored_id = stored_deezer_ids.get(name.lower())
        if stored_id and stored_id not in seen_artist_ids:
            deezer_id = stored_id
            display_name = name
            seen_artist_ids.add(deezer_id)
            discovered_artists.append({
                "deezer_id": deezer_id,
                "name": display_name,
                "name_lower": display_name.lower(),
                "image_url": None,
                "fans_deezer": 0,
            })
        else:
            # Fallback: resolve via Deezer name search
            candidates = music_client.search_artist_candidates_deezer(name, limit=1)
            if not candidates:
                continue
            artist = candidates[0]
            deezer_id = str(artist.get("deezer_id") or artist.get("id", ""))
            if not deezer_id or deezer_id in seen_artist_ids:
                continue
            display_name = artist.get("name", name)
            seen_artist_ids.add(deezer_id)
            discovered_artists.append({
                "deezer_id": deezer_id,
                "name": display_name,
                "name_lower": display_name.lower(),
                "image_url": artist.get("image_url") or artist.get("picture_medium") or None,
                "fans_deezer": artist.get("nb_fan") or 0,
            })

        # Fetch all albums in window, then apply kind preference per artist
        artist_releases_in_window = []
        albums = music_client.get_artist_albums_deezer(deezer_id)
        for album in albums:
            rel_date = album.get("release_date", "") or ""
            # Skip albums with no release date OR older than the cutoff window
            if not rel_date or rel_date < cutoff_date:
                continue

            record_type = (album.get("record_type") or "album").lower()
            title = album.get("title", "")
            title_lower = title.lower()

            if any(kw in title_lower for kw in ignore_kw):
                keyword_filtered.append({
                    "id": str(album["id"]),
                    "artist_deezer_id": deezer_id,
                    "artist_name": display_name,
                    "title": title,
                    "record_type": record_type,
                    "release_date": rel_date or None,
                    "cover_url": album.get("artwork_url") or None,
                    "in_library": False,
                })
                continue

            artist_releases_in_window.append({
                "id": str(album["id"]),
                "artist_deezer_id": deezer_id,
                "artist_name": display_name,
                "title": title,
                "record_type": record_type,
                "release_date": rel_date or None,
                "cover_url": album.get("artwork_url") or None,
            })

        # Apply kind preference after collecting all releases for this artist
        preferred = _apply_kind_preference(artist_releases_in_window, release_kinds)
        discovered_releases.extend(preferred)

        if run_id and (idx == total_names or idx % 5 == 0):
            _emit_pipeline_progress(
                pipeline="new_music",
                run_id=run_id,
                stage="releases",
                processed=idx,
                total=max(total_names, 1),
                message=f"Processed {idx}/{total_names} artists",
            )

    logger.info(
        "new_music: fetched artists=%d releases=%d filtered=%d (lookback=%dd, kinds_mode=%s)",
        len(discovered_artists), len(discovered_releases), len(keyword_filtered), lookback_days, release_kinds
    )
    return discovered_artists, discovered_releases, keyword_filtered


# ---------------------------------------------------------------------------
# Step 4: Write to DB
# ---------------------------------------------------------------------------

def write_discovered(artists: list[dict], releases: list[dict]) -> None:
    """
    Upsert discovered artists and releases into forge tables.
    Uses ON CONFLICT DO UPDATE to keep data fresh.
    """
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    with _connect() as conn:
        for a in artists:
            conn.execute(
                """
                INSERT INTO forge_discovered_artists
                    (deezer_id, name, name_lower, image_url, fans_deezer, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(deezer_id) DO UPDATE SET
                    name        = excluded.name,
                    name_lower  = excluded.name_lower,
                    image_url   = COALESCE(excluded.image_url, image_url),
                    fans_deezer = COALESCE(excluded.fans_deezer, fans_deezer),
                    fetched_at  = excluded.fetched_at
                """,
                (a["deezer_id"], a["name"], a["name_lower"], a.get("image_url"), a.get("fans_deezer", 0), now)
            )

        for r in releases:
            conn.execute(
                """
                INSERT INTO forge_discovered_releases
                    (id, artist_deezer_id, title, record_type, release_date, cover_url, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title        = excluded.title,
                    record_type  = excluded.record_type,
                    release_date = COALESCE(excluded.release_date, release_date),
                    cover_url    = COALESCE(excluded.cover_url, cover_url),
                    fetched_at   = excluded.fetched_at
                """,
                (r["id"], r["artist_deezer_id"], r["title"], r.get("record_type"), r.get("release_date"), r.get("cover_url"), now)
            )


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def run_new_music_pipeline(config_override: dict | None = None) -> dict:
    """
    Run the full New Music pipeline.
    Returns summary: {artists_checked, releases_found, filtered_releases: [...]} 
    """
    run_id = str(uuid.uuid4())
    try:
        cfg = get_config()
        if config_override:
            cfg.update({k: v for k, v in config_override.items() if k in NM_DEFAULTS})

        period = cfg["nm_period"]
        min_scrobbles = int(cfg["nm_min_scrobbles"])
        lookback_days = int(cfg["nm_lookback_days"])
        match_mode = cfg["nm_match_mode"]
        release_kinds = cfg["nm_release_kinds"]
        ignore_keywords = cfg["nm_ignore_keywords"]
        ignore_artists = cfg["nm_ignore_artists"]

        _emit_pipeline_progress(
            pipeline="new_music",
            run_id=run_id,
            stage="history",
            processed=0,
            total=1,
            message="Loading listening history",
        )

        # Clear stale results from previous runs (Tier 2 - rebuildable)
        with _connect() as conn:
            conn.execute("DELETE FROM forge_discovered_releases")
            conn.execute("DELETE FROM forge_discovered_artists")
        logger.info("new_music: cleared stale forge_discovered tables")

        # Step 1: seed artists from listening history
        seeds = get_seed_artists(period, min_scrobbles)
        seed_names = [s["name"] for s in seeds]
        _emit_pipeline_progress(
            pipeline="new_music",
            run_id=run_id,
            stage="history",
            processed=1,
            total=1,
            message=f"Loaded {len(seeds)} seed artists",
        )

        # Step 2: fetch recent releases from those seed artists directly
        artists, releases, filtered = fetch_releases_for_neighbors(
            seed_names,
            lookback_days,
            match_mode,
            release_kinds,
            ignore_keywords,
            ignore_artists,
            run_id=run_id,
        )

        _emit_pipeline_progress(
            pipeline="new_music",
            run_id=run_id,
            stage="persist",
            processed=0,
            total=1,
            message="Saving discovered releases",
        )

        # Step 3: persist to forge tables
        write_discovered(artists, releases)
        _emit_pipeline_progress(
            pipeline="new_music",
            run_id=run_id,
            stage="persist",
            processed=1,
            total=1,
            message="Saved discovered releases",
        )

        logger.info(
            "new_music: pipeline complete - seeds=%d artists_resolved=%d releases_stored=%d filtered=%d",
            len(seeds), len(artists), len(releases), len(filtered)
        )

        summary = {
            "artists_checked": len(seeds),
            "releases_found": len(releases),
            "filtered_releases": filtered,
        }
        _emit_pipeline_complete(pipeline="new_music", run_id=run_id, summary=summary)
        return summary
    except Exception as exc:
        _emit_pipeline_error(pipeline="new_music", run_id=run_id, message=str(exc))
        raise

