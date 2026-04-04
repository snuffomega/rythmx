"""
discovery_runner.py - Forge Custom Discovery pipeline.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app import config
from app.db import get_library_reader, rythmx_store
from app.services.forge import new_music_runner

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
    "exclude_owned_artists": False,
    "avoid_repeat_tracks": True,
    "track_repeat_cooldown_days": 42,
    "cache_ttl_days": 30,
    "fetch_wait_timeout_s": 600,
    "build_name_override": "",
    "ignore_keywords": "",
    "ignore_artists": "",
}

_INT_KEYS = {
    "closeness",
    "min_scrobbles",
    "max_tracks",
    "schedule_weekday",
    "schedule_hour",
    "track_repeat_cooldown_days",
    "cache_ttl_days",
    "fetch_wait_timeout_s",
}
_BOOL_KEYS = {"auto_publish", "schedule_enabled", "dry_run", "exclude_owned_artists", "avoid_repeat_tracks"}
_SEED_PERIODS = {"7day", "1month", "3month", "6month", "12month", "overall"}
_RUN_MODES = {"build", "fetch"}

_CONFIG_PREFIX = "fd_"
_RESULTS_KEY = "fd_last_results_json"

_MAX_SEEDS = 200
_MAX_FRONTIER_PER_HOP = 90
_MAX_NEIGHBORS_PER_ARTIST = 18
_MAX_CANDIDATE_ARTISTS = 1200
_TOP_TRACK_FETCH_LIMIT = 50

_BAND_RULES = {
    "strict": {"max_hop": 1, "rank_min": 1, "rank_max": 3, "rank_mid": 2.0},
    "balanced": {"max_hop": 2, "rank_min": 2, "rank_max": 5, "rank_mid": 3.5},
    "adventurous": {"max_hop": 3, "rank_min": 4, "rank_max": 7, "rank_mid": 5.5},
}


def _setting_key(api_key: str) -> str:
    return f"{_CONFIG_PREFIX}{api_key}"


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _normalize_name(value: str) -> str:
    return str(value or "").strip().lower()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_set(raw: str) -> set[str]:
    return {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}


def _chunked(values: list[str], size: int = 300) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _normalize_owned_track_id(match: Any) -> str | None:
    if match is None:
        return None
    if isinstance(match, dict):
        candidate = (
            match.get("id")
            or match.get("track_id")
            or match.get("plex_rating_key")
            or match.get("navidrome_track_id")
        )
        tid = str(candidate or "").strip()
        return tid or None
    tid = str(match).strip()
    return tid or None


def get_config() -> dict[str, Any]:
    """Return discovery config from app_settings, merged with defaults."""
    raw = rythmx_store.get_all_settings()
    cfg: dict[str, Any] = {}
    for key, default in DISCOVERY_DEFAULTS.items():
        val = raw.get(_setting_key(key))
        if val is None:
            cfg[key] = default
        elif key in _BOOL_KEYS:
            cfg[key] = _is_truthy(val)
        elif key in _INT_KEYS:
            cfg[key] = _safe_int(val, int(default))
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
            if key == "track_repeat_cooldown_days" and not (1 <= iv <= 365):
                return "track_repeat_cooldown_days must be between 1 and 365"
            if key == "cache_ttl_days" and not (1 <= iv <= 365):
                return "cache_ttl_days must be between 1 and 365"
            if key == "fetch_wait_timeout_s" and not (30 <= iv <= 7200):
                return "fetch_wait_timeout_s must be between 30 and 7200"

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


def _bands_for_closeness(closeness: int) -> tuple[list[str], bool]:
    if closeness <= 3:
        return ["strict"], False
    if closeness <= 6:
        return ["balanced"], False
    if closeness <= 8:
        return ["adventurous"], False
    return ["strict", "balanced", "adventurous"], True


def _parse_similar_artists_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    results: list[dict[str, Any]] = []
    for entry in parsed:
        if isinstance(entry, str):
            name = entry.strip()
            if name:
                results.append({"name": name, "match": 0.5, "source": "library"})
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        match_raw = entry.get("match")
        try:
            match_val = float(match_raw) if match_raw is not None else 0.5
        except Exception:
            match_val = 0.5
        if match_val <= 0:
            match_val = 0.25
        if match_val > 1:
            match_val = 1.0
        results.append(
            {
                "name": name,
                "match": match_val,
                "source": str(entry.get("source") or "library"),
            }
        )
    return results


def _load_local_neighbors(frontier: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not frontier:
        return {}
    lowers = [f["name_lower"] for f in frontier if f.get("name_lower")]
    if not lowers:
        return {}

    mapping: dict[str, list[dict[str, Any]]] = {}
    with _connect() as conn:
        for chunk in _chunked(lowers):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT name_lower, similar_artists_json
                FROM lib_artists
                WHERE removed_at IS NULL AND name_lower IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            for row in rows:
                mapping[str(row["name_lower"])] = _parse_similar_artists_json(row["similar_artists_json"])
    return mapping


def _load_history_index() -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT deezer_track_id, last_recommended_at, recommended_count
            FROM forge_custom_discovery_track_history
            """
        ).fetchall()
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row["deezer_track_id"] or "").strip()
        if not tid:
            continue
        index[tid] = {
            "last_recommended_at": str(row["last_recommended_at"] or ""),
            "recommended_count": int(row["recommended_count"] or 0),
        }
    return index


def _cleanup_expired_cache() -> None:
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM forge_custom_discovery_artist_cache WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        conn.execute(
            "DELETE FROM forge_custom_discovery_track_cache WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )


def _resolve_deezer_artist_metadata(
    candidates: list[dict[str, Any]],
    cache_ttl_days: int,
) -> list[dict[str, Any]]:
    """
    Resolve Deezer artist IDs for candidate names using:
      1) custom artist cache
      2) lib_artists fast path
      3) Deezer search fallback
    """
    if not candidates:
        return []

    from app.clients.music_client import search_artist_candidates_deezer

    now = datetime.utcnow()
    expires_at = (now + timedelta(days=cache_ttl_days)).strftime("%Y-%m-%dT%H:%M:%S")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    lowers = [c["name_lower"] for c in candidates if c.get("name_lower")]

    cache_rows: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        for chunk in _chunked(lowers):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT artist_name_lower, artist_name, deezer_artist_id, image_url, hop, similarity, source
                FROM forge_custom_discovery_artist_cache
                WHERE artist_name_lower IN ({placeholders})
                  AND (expires_at IS NULL OR expires_at >= ?)
                """,
                tuple(chunk) + (now_iso,),
            ).fetchall()
            for row in rows:
                cache_rows[str(row["artist_name_lower"])] = dict(row)

    lib_rows: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        for chunk in _chunked(lowers):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT la.name_lower,
                       la.name,
                       la.deezer_artist_id,
                       ic.image_url
                FROM lib_artists la
                LEFT JOIN image_cache ic
                    ON ic.entity_type = 'artist' AND ic.entity_key = la.id
                WHERE la.removed_at IS NULL AND la.name_lower IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            for row in rows:
                lib_rows[str(row["name_lower"])] = dict(row)

    resolved: list[dict[str, Any]] = []
    with _connect() as conn:
        for candidate in candidates:
            name_lower = candidate["name_lower"]
            payload = {
                "artist": candidate["name"],
                "artist_name_lower": name_lower,
                "deezer_artist_id": None,
                "image": None,
                "hop": int(candidate["hop"]),
                "similarity": float(candidate["similarity"]),
                "source": candidate.get("source") or "graph",
            }

            cache_row = cache_rows.get(name_lower)
            if cache_row and cache_row.get("deezer_artist_id"):
                payload["artist"] = cache_row.get("artist_name") or payload["artist"]
                payload["deezer_artist_id"] = str(cache_row.get("deezer_artist_id") or "").strip() or None
                payload["image"] = cache_row.get("image_url")
            else:
                lib_row = lib_rows.get(name_lower)
                if lib_row and lib_row.get("deezer_artist_id"):
                    payload["artist"] = lib_row.get("name") or payload["artist"]
                    payload["deezer_artist_id"] = str(lib_row.get("deezer_artist_id") or "").strip() or None
                    payload["image"] = lib_row.get("image_url")
                else:
                    hits = search_artist_candidates_deezer(payload["artist"], limit=1)
                    if hits:
                        payload["artist"] = str(hits[0].get("name") or payload["artist"])
                        payload["deezer_artist_id"] = str(hits[0].get("id") or "").strip() or None

            if not payload["deezer_artist_id"]:
                continue

            resolved.append(payload)

            conn.execute(
                """
                INSERT INTO forge_custom_discovery_artist_cache
                    (artist_name_lower, artist_name, deezer_artist_id, image_url, hop, similarity, source, cached_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_name_lower) DO UPDATE SET
                    artist_name = excluded.artist_name,
                    deezer_artist_id = excluded.deezer_artist_id,
                    image_url = COALESCE(excluded.image_url, image_url),
                    hop = excluded.hop,
                    similarity = excluded.similarity,
                    source = excluded.source,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at
                """,
                (
                    payload["artist_name_lower"],
                    payload["artist"],
                    payload["deezer_artist_id"],
                    payload["image"],
                    payload["hop"],
                    payload["similarity"],
                    payload["source"],
                    now_iso,
                    expires_at,
                ),
            )

    return resolved


def _get_artist_top_tracks_cached(
    *,
    deezer_artist_id: str,
    artist_name: str,
    artist_name_lower: str,
    cache_ttl_days: int,
) -> list[dict[str, Any]]:
    from app.clients.music_client import get_deezer_artist_top_tracks

    now = datetime.utcnow()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    with _connect() as conn:
        cached_rows = conn.execute(
            """
            SELECT deezer_track_id, deezer_artist_id, artist_name, artist_name_lower,
                   track_title, rank_position, deezer_rank, preview_url, album_title, album_cover_url
            FROM forge_custom_discovery_track_cache
            WHERE deezer_artist_id = ?
              AND (expires_at IS NULL OR expires_at >= ?)
            ORDER BY rank_position ASC, deezer_rank DESC
            """,
            (deezer_artist_id, now_iso),
        ).fetchall()

        if cached_rows:
            return [
                {
                    "deezer_track_id": str(row["deezer_track_id"]),
                    "deezer_artist_id": str(row["deezer_artist_id"] or deezer_artist_id),
                    "artist_name": row["artist_name"] or artist_name,
                    "artist_name_lower": row["artist_name_lower"] or artist_name_lower,
                    "track_title": row["track_title"] or "",
                    "rank_position": int(row["rank_position"] or 0),
                    "deezer_rank": int(row["deezer_rank"] or 0),
                    "preview_url": row["preview_url"] or "",
                    "album_title": row["album_title"] or "",
                    "album_cover_url": row["album_cover_url"] or "",
                }
                for row in cached_rows
            ]

    fetched = get_deezer_artist_top_tracks(deezer_artist_id, limit=_TOP_TRACK_FETCH_LIMIT)
    if not fetched:
        return []

    expires_at = (now + timedelta(days=cache_ttl_days)).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[dict[str, Any]] = []
    with _connect() as conn:
        for track in fetched:
            deezer_track_id = str(track.get("id") or "").strip()
            track_title = str(track.get("title") or "").strip()
            if not deezer_track_id or not track_title:
                continue

            row_payload = {
                "deezer_track_id": deezer_track_id,
                "deezer_artist_id": deezer_artist_id,
                "artist_name": str(track.get("artist_name") or artist_name),
                "artist_name_lower": artist_name_lower,
                "track_title": track_title,
                "rank_position": int(track.get("rank_position") or 0),
                "deezer_rank": int(track.get("deezer_rank") or 0),
                "preview_url": str(track.get("preview_url") or ""),
                "album_title": str(track.get("album_title") or ""),
                "album_cover_url": str(track.get("album_cover_url") or ""),
            }
            rows.append(row_payload)

            conn.execute(
                """
                INSERT INTO forge_custom_discovery_track_cache
                    (deezer_track_id, deezer_artist_id, artist_name, artist_name_lower, track_title, track_title_lower,
                     rank_position, deezer_rank, preview_url, album_title, album_cover_url, cached_at, expires_at)
                VALUES (?, ?, ?, ?, ?, lower(?), ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deezer_track_id) DO UPDATE SET
                    deezer_artist_id = excluded.deezer_artist_id,
                    artist_name = excluded.artist_name,
                    artist_name_lower = excluded.artist_name_lower,
                    track_title = excluded.track_title,
                    track_title_lower = excluded.track_title_lower,
                    rank_position = excluded.rank_position,
                    deezer_rank = excluded.deezer_rank,
                    preview_url = excluded.preview_url,
                    album_title = excluded.album_title,
                    album_cover_url = excluded.album_cover_url,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at
                """,
                (
                    row_payload["deezer_track_id"],
                    row_payload["deezer_artist_id"],
                    row_payload["artist_name"],
                    row_payload["artist_name_lower"],
                    row_payload["track_title"],
                    row_payload["track_title"],
                    row_payload["rank_position"],
                    row_payload["deezer_rank"],
                    row_payload["preview_url"],
                    row_payload["album_title"],
                    row_payload["album_cover_url"],
                    now_iso,
                    expires_at,
                ),
            )

    rows.sort(key=lambda t: (t["rank_position"] or 9999, -(t["deezer_rank"] or 0)))
    return rows


def _expand_artist_graph(
    *,
    seed_names: list[str],
    max_hop: int,
    ignore_artists: set[str],
) -> list[dict[str, Any]]:
    seed_set = {_normalize_name(name) for name in seed_names if _normalize_name(name)}
    if not seed_set:
        return []

    frontier: list[dict[str, Any]] = [
        {
            "name": name,
            "name_lower": _normalize_name(name),
            "hop": 0,
            "similarity": 1.0,
            "source": "seed",
        }
        for name in seed_names
        if _normalize_name(name)
    ]

    seen: dict[str, dict[str, Any]] = {}
    lastfm_enabled = bool(config.LASTFM_API_KEY)
    lastfm_client = None
    if lastfm_enabled:
        try:
            from app.clients import last_fm_client as _lfm

            lastfm_client = _lfm
        except Exception:
            lastfm_client = None

    for hop in range(1, max_hop + 1):
        if not frontier:
            break

        next_frontier: dict[str, dict[str, Any]] = {}
        local_map = _load_local_neighbors(frontier)

        for node in frontier[:_MAX_FRONTIER_PER_HOP]:
            neighbors = list(local_map.get(node["name_lower"]) or [])

            if lastfm_client is not None and (not neighbors or hop > 1):
                try:
                    remote = lastfm_client.get_similar_artists(
                        node["name"], limit=_MAX_NEIGHBORS_PER_ARTIST
                    )
                    for r in remote:
                        nname = str(r.get("name") or "").strip()
                        if not nname:
                            continue
                        match_val = float(r.get("match") or 0.25)
                        if match_val <= 0:
                            match_val = 0.25
                        neighbors.append(
                            {"name": nname, "match": min(match_val, 1.0), "source": "lastfm"}
                        )
                except Exception as exc:
                    logger.debug("discovery: Last.fm similar lookup failed for '%s': %s", node["name"], exc)

            for neighbor in neighbors[:_MAX_NEIGHBORS_PER_ARTIST]:
                name = str(neighbor.get("name") or "").strip()
                name_lower = _normalize_name(name)
                if not name_lower:
                    continue
                if name_lower in seed_set:
                    continue
                if name_lower in ignore_artists:
                    continue

                try:
                    edge_score = float(neighbor.get("match") or 0.25)
                except Exception:
                    edge_score = 0.25
                if edge_score <= 0:
                    edge_score = 0.25
                if edge_score > 1:
                    edge_score = 1.0

                similarity = round(float(node["similarity"]) * edge_score, 6)
                candidate = {
                    "name": name,
                    "name_lower": name_lower,
                    "hop": hop,
                    "similarity": similarity,
                    "source": str(neighbor.get("source") or "graph"),
                }

                existing = seen.get(name_lower)
                if existing is None or hop < int(existing["hop"]) or similarity > float(existing["similarity"]):
                    seen[name_lower] = candidate

                frontier_existing = next_frontier.get(name_lower)
                if frontier_existing is None or similarity > float(frontier_existing["similarity"]):
                    next_frontier[name_lower] = candidate

        frontier = sorted(
            next_frontier.values(),
            key=lambda x: (float(x["similarity"]), -int(x["hop"])),
            reverse=True,
        )[:_MAX_FRONTIER_PER_HOP]

        if len(seen) >= _MAX_CANDIDATE_ARTISTS:
            break

    return sorted(
        seen.values(),
        key=lambda x: (float(x["similarity"]), -int(x["hop"]), x["name"]),
        reverse=True,
    )[:_MAX_CANDIDATE_ARTISTS]


def _matches_rank_band(track_rank: int, band_name: str) -> bool:
    rule = _BAND_RULES[band_name]
    return rule["rank_min"] <= track_rank <= rule["rank_max"]


def _select_track_for_artist(
    *,
    artist: dict[str, Any],
    tracks: list[dict[str, Any]],
    band_name: str,
    allow_out_of_band: bool,
    history_index: dict[str, dict[str, Any]],
    avoid_repeat_tracks: bool,
    cooldown_cutoff: datetime,
    ignore_keywords: set[str],
) -> tuple[dict[str, Any] | None, str]:
    if not tracks:
        return None, "no_tracks"

    rule = _BAND_RULES[band_name]
    if int(artist["hop"]) > int(rule["max_hop"]):
        return None, "hop_out_of_band"

    def _track_sort_key(t: dict[str, Any]) -> tuple[float, int, int]:
        tid = str(t.get("deezer_track_id") or "")
        hist = history_index.get(tid) or {}
        count = int(hist.get("recommended_count") or 0)
        rank_position = int(t.get("rank_position") or 999)
        distance = abs(rank_position - float(rule["rank_mid"]))
        return (float(count), int(distance * 10), rank_position)

    filtered_tracks = []
    for t in tracks:
        title_lower = _normalize_name(t.get("track_title", ""))
        if ignore_keywords and any(kw in title_lower for kw in ignore_keywords):
            continue
        filtered_tracks.append(t)
    if not filtered_tracks:
        return None, "all_filtered_keywords"

    in_band = [t for t in filtered_tracks if _matches_rank_band(int(t.get("rank_position") or 0), band_name)]
    out_band = [t for t in filtered_tracks if t not in in_band]

    candidates = in_band
    source_flag = "in_band"

    if not candidates and allow_out_of_band:
        candidates = out_band
        source_flag = "fallback_out_of_band"

    if not candidates:
        return None, "no_rank_candidates"

    if avoid_repeat_tracks:
        fresh = []
        stale = []
        for t in candidates:
            tid = str(t.get("deezer_track_id") or "")
            hist = history_index.get(tid)
            if not hist:
                fresh.append(t)
                continue
            raw_last = str(hist.get("last_recommended_at") or "")
            try:
                last_dt = datetime.fromisoformat(raw_last)
            except Exception:
                fresh.append(t)
                continue
            if last_dt < cooldown_cutoff:
                fresh.append(t)
            else:
                stale.append(t)

        if fresh:
            fresh.sort(key=_track_sort_key)
            return fresh[0], source_flag
        stale.sort(key=_track_sort_key)
        return stale[0], f"{source_flag}:repeat_fallback"

    candidates.sort(key=_track_sort_key)
    return candidates[0], source_flag


def _upsert_run_start(run_id: str, cfg: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO forge_custom_discovery_runs (run_id, started_at, config_json, status)
            VALUES (?, ?, ?, 'running')
            """,
            (run_id, _now_iso(), json.dumps(cfg)),
        )


def _upsert_run_finish(run_id: str, summary: dict[str, Any], status: str = "completed") -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE forge_custom_discovery_runs
            SET finished_at = ?, summary_json = ?, status = ?
            WHERE run_id = ?
            """,
            (_now_iso(), json.dumps(summary), status, run_id),
        )


def _persist_track_history(recommendations: list[dict[str, Any]]) -> None:
    if not recommendations:
        return
    now = _now_iso()
    with _connect() as conn:
        for rec in recommendations:
            deezer_track_id = str(rec.get("deezer_track_id") or "").strip()
            if not deezer_track_id:
                continue
            conn.execute(
                """
                INSERT INTO forge_custom_discovery_track_history
                    (deezer_track_id, deezer_artist_id, artist_name, artist_name_lower, track_title,
                     first_seen_at, last_recommended_at, recommended_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(deezer_track_id) DO UPDATE SET
                    deezer_artist_id = COALESCE(excluded.deezer_artist_id, deezer_artist_id),
                    artist_name = COALESCE(excluded.artist_name, artist_name),
                    artist_name_lower = COALESCE(excluded.artist_name_lower, artist_name_lower),
                    track_title = COALESCE(excluded.track_title, track_title),
                    last_recommended_at = excluded.last_recommended_at,
                    recommended_count = COALESCE(recommended_count, 0) + 1
                """,
                (
                    deezer_track_id,
                    rec.get("deezer_artist_id"),
                    rec.get("artist"),
                    _normalize_name(rec.get("artist", "")),
                    rec.get("track_name"),
                    now,
                    now,
                ),
            )


def run_discovery_pipeline(config_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Run Custom Discovery and persist results for /forge/discovery/results.
    """
    _cleanup_expired_cache()

    cfg = get_config()
    if config_override:
        cfg.update({k: v for k, v in config_override.items() if k in DISCOVERY_DEFAULTS})

    closeness = int(cfg.get("closeness", DISCOVERY_DEFAULTS["closeness"]))
    seed_period = str(cfg.get("seed_period", DISCOVERY_DEFAULTS["seed_period"]))
    min_scrobbles = int(cfg.get("min_scrobbles", DISCOVERY_DEFAULTS["min_scrobbles"]))
    max_tracks = int(cfg.get("max_tracks", DISCOVERY_DEFAULTS["max_tracks"]))
    run_mode = str(cfg.get("run_mode", DISCOVERY_DEFAULTS["run_mode"]))
    exclude_owned_artists = bool(cfg.get("exclude_owned_artists", DISCOVERY_DEFAULTS["exclude_owned_artists"]))
    avoid_repeat_tracks = bool(cfg.get("avoid_repeat_tracks", DISCOVERY_DEFAULTS["avoid_repeat_tracks"]))
    cooldown_days = int(
        cfg.get("track_repeat_cooldown_days", DISCOVERY_DEFAULTS["track_repeat_cooldown_days"])
    )
    cache_ttl_days = int(cfg.get("cache_ttl_days", DISCOVERY_DEFAULTS["cache_ttl_days"]))
    ignore_artists = _parse_csv_set(cfg.get("ignore_artists", ""))
    ignore_keywords = _parse_csv_set(cfg.get("ignore_keywords", ""))

    bands, wildcard = _bands_for_closeness(closeness)
    max_hop = max(_BAND_RULES[b]["max_hop"] for b in bands)
    overfetch_target = min(max(max_tracks * 2, max_tracks + 20), 250)
    cooldown_cutoff = datetime.utcnow() - timedelta(days=cooldown_days)

    fetch_enabled = _is_truthy(rythmx_store.get_setting("fetch_enabled", "false"))
    include_missing = fetch_enabled and run_mode == "fetch"

    run_id = str(uuid.uuid4())
    _upsert_run_start(run_id, cfg)

    try:
        seeds = new_music_runner.get_seed_artists(seed_period, min_scrobbles)[:_MAX_SEEDS]
        seed_names = [str(s.get("name") or "").strip() for s in seeds if str(s.get("name") or "").strip()]
        seed_set = {_normalize_name(name) for name in seed_names}

        graph_candidates = _expand_artist_graph(
            seed_names=seed_names,
            max_hop=max_hop,
            ignore_artists=ignore_artists,
        )

        if exclude_owned_artists:
            with _connect() as conn:
                owned_rows = conn.execute(
                    "SELECT name_lower FROM lib_artists WHERE removed_at IS NULL"
                ).fetchall()
            owned_set = {str(r["name_lower"]) for r in owned_rows}
            graph_candidates = [c for c in graph_candidates if c["name_lower"] not in owned_set]

        graph_candidates = [c for c in graph_candidates if c["name_lower"] not in seed_set]
        graph_candidates = graph_candidates[: max(overfetch_target * 2, 120)]

        resolved_artists = _resolve_deezer_artist_metadata(
            graph_candidates,
            cache_ttl_days=cache_ttl_days,
        )

        history_index = _load_history_index()
        reader = get_library_reader()

        recommendations: list[dict[str, Any]] = []
        owned_count = 0
        missing_count = 0
        skipped_no_track = 0
        skipped_no_match_build_mode = 0
        band_cursor = 0

        for artist in resolved_artists:
            if len(recommendations) >= max_tracks:
                break

            if wildcard:
                primary_band = bands[band_cursor % len(bands)]
                band_cursor += 1
                band_order = [primary_band] + [b for b in bands if b != primary_band]
            else:
                band_order = list(bands)

            tracks = _get_artist_top_tracks_cached(
                deezer_artist_id=str(artist["deezer_artist_id"]),
                artist_name=str(artist["artist"]),
                artist_name_lower=str(artist["artist_name_lower"]),
                cache_ttl_days=cache_ttl_days,
            )
            if not tracks:
                skipped_no_track += 1
                continue

            chosen = None
            chosen_band = band_order[0]
            chosen_reason = "no_rank_candidates"

            for band_name in band_order:
                selected, reason = _select_track_for_artist(
                    artist=artist,
                    tracks=tracks,
                    band_name=band_name,
                    allow_out_of_band=True,
                    history_index=history_index,
                    avoid_repeat_tracks=avoid_repeat_tracks,
                    cooldown_cutoff=cooldown_cutoff,
                    ignore_keywords=ignore_keywords,
                )
                if selected is not None:
                    chosen = selected
                    chosen_band = band_name
                    chosen_reason = reason
                    break
                chosen_reason = reason

            if chosen is None:
                skipped_no_track += 1
                continue

            deezer_track_id = str(chosen.get("deezer_track_id") or "").strip()
            track_name = str(chosen.get("track_title") or "").strip()
            artist_name = str(artist["artist"])
            library_track_id = None

            if deezer_track_id:
                library_track_id = _normalize_owned_track_id(reader.check_owned_deezer(deezer_track_id))
            if not library_track_id:
                library_track_id = _normalize_owned_track_id(reader.find_track_by_name(artist_name, track_name))

            is_owned = bool(library_track_id)
            if is_owned:
                owned_count += 1
            else:
                missing_count += 1
                if not include_missing:
                    skipped_no_match_build_mode += 1
                    continue

            recommendations.append(
                {
                    "artist": artist_name,
                    "image": artist.get("image"),
                    "track_name": track_name,
                    "album_name": chosen.get("album_title") or "",
                    "album_cover_url": chosen.get("album_cover_url") or artist.get("image"),
                    "deezer_artist_id": artist.get("deezer_artist_id"),
                    "deezer_track_id": deezer_track_id,
                    "track_id": library_track_id,
                    "is_owned": is_owned,
                    "hop": int(artist["hop"]),
                    "similarity": float(artist["similarity"]),
                    "rank_position": int(chosen.get("rank_position") or 0),
                    "deezer_rank": int(chosen.get("deezer_rank") or 0),
                    "band": chosen_band,
                    "reason": f"{chosen_band}:{chosen_reason}",
                    "tags": [],
                }
            )

            if len(recommendations) >= max_tracks:
                break

        rythmx_store.set_setting(_RESULTS_KEY, json.dumps(recommendations))
        _persist_track_history(recommendations)

        summary = {
            "artists_found": len(recommendations),
            "artists": recommendations,
            "run_id": run_id,
            "seed_artists_count": len(seed_names),
            "candidate_artist_count": len(graph_candidates),
            "resolved_artist_count": len(resolved_artists),
            "target_tracks": max_tracks,
            "built_tracks": len(recommendations),
            "owned_count": owned_count,
            "missing_count": missing_count,
            "include_missing": include_missing,
            "closeness": closeness,
            "bands": bands,
            "wildcard": wildcard,
            "skipped_no_track": skipped_no_track,
            "skipped_no_match_build_mode": skipped_no_match_build_mode,
            "avoid_repeat_tracks": avoid_repeat_tracks,
            "track_repeat_cooldown_days": cooldown_days,
            "exclude_owned_artists": exclude_owned_artists,
            "cache_ttl_days": cache_ttl_days,
        }
        _upsert_run_finish(run_id, summary, status="completed")
        logger.info(
            "discovery: run complete (run_id=%s, built=%d, target=%d, owned=%d, missing=%d, include_missing=%s)",
            run_id,
            len(recommendations),
            max_tracks,
            owned_count,
            missing_count,
            include_missing,
        )
        return summary
    except Exception as exc:
        _upsert_run_finish(
            run_id,
            {"error": str(exc), "run_id": run_id},
            status="error",
        )
        raise
