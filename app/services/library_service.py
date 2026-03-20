"""
library_service.py — ETL orchestrator for the native library engine (Phase 10).

Three-stage pipeline for the Plex backend:
  Stage 1 SYNC    — Walk Plex API → write lib_* tables (delegates to plex_reader)
  Stage 2 ENRICH  — For each lib_album with no itunes_album_id, query iTunes → Deezer
  Stage 3 STATUS  — Return combined sync + enrich progress for the Settings UI

Enrichment APIs (SoulSync, Spotify, Last.fm, Deezer, iTunes) populate metadata after sync.
The enrich stage is resumable: only processes albums where itunes_album_id IS NULL
AND deezer_id IS NULL, so interrupted runs pick up where they left off.
"""
import concurrent.futures
import difflib
import logging
import re
import sqlite3
import threading
from datetime import datetime
from app import config
from app.db import rythmx_store
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)

_ITUNES_BASE = "https://itunes.apple.com"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect():
    """WAL connection to rythmx.db for lib_* read/write."""
    conn = sqlite3.connect(config.RYTHMX_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\(\[](single|ep|deluxe|deluxe\s+edition|explicit|remaster(ed)?|'
    r'expanded|anniversary\s+edition|bonus\s+track[s]?|special\s+edition|'
    r'reissue)[\s\w]*[\)\]]',
    re.IGNORECASE,
)

def _strip_title_suffixes(title: str) -> str:
    """Strip Plex-appended suffixes like [Single], [EP], (Deluxe Edition) before search."""
    return _TITLE_SUFFIX_RE.sub("", title).strip()


def _itunes_search_album(artist_name: str, album_title: str) -> dict | None:
    """
    Query iTunes Search API for a specific album.
    Returns a dict with itunes_album_id and api_title, or None on miss/error.
    Rate-limited via shared DomainRateLimiter (20 req/min).
    """
    import requests

    rate_limiter.acquire("itunes")
    try:
        resp = requests.get(
            f"{_ITUNES_BASE}/search",
            params={
                "term": f"{artist_name} {album_title}",
                "media": "music",
                "entity": "album",
                "limit": 5,
                "attribute": "albumTerm",
            },
            timeout=10,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("itunes")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("itunes")
        results = resp.json().get("results", [])
    except Exception as e:
        logger.debug("iTunes search failed for '%s / %s': %s", artist_name, album_title, e)
        return None

    if not results:
        return None

    # Find best match: exact artist + album name (case-insensitive)
    artist_lower = artist_name.lower()
    title_lower = album_title.lower()
    for item in results:
        a = (item.get("artistName") or "").lower()
        t = (item.get("collectionName") or "").lower()
        if a == artist_lower and t == title_lower:
            return {
                "itunes_album_id": str(item["collectionId"]),
                "api_title": item.get("collectionName", ""),
                "itunes_artist_id": str(item.get("artistId", "")),
            }

    # Fallback: partial title match (first result where artist matches)
    for item in results:
        a = (item.get("artistName") or "").lower()
        t = (item.get("collectionName") or "").lower()
        if a == artist_lower and title_lower in t:
            return {
                "itunes_album_id": str(item["collectionId"]),
                "api_title": item.get("collectionName", ""),
                "itunes_artist_id": str(item.get("artistId", "")),
            }

    return None


def _deezer_search_album(artist_name: str, album_title: str) -> dict | None:
    """
    Query Deezer Search API for a specific album.
    Returns a dict with deezer_id and api_title, or None on miss/error.
    Rate-limited via shared DomainRateLimiter (50 req/min).
    """
    import requests

    rate_limiter.acquire("deezer")
    try:
        resp = requests.get(
            "https://api.deezer.com/search/album",
            params={"q": f'artist:"{artist_name}" album:"{album_title}"', "limit": 5},
            timeout=10,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("deezer")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("deezer")
        items = resp.json().get("data", [])
    except Exception as e:
        logger.debug("Deezer search failed for '%s / %s': %s", artist_name, album_title, e)
        return None

    if not items:
        return None

    artist_lower = artist_name.lower()
    title_lower = album_title.lower()
    for item in items:
        a = (item.get("artist", {}).get("name") or "").lower()
        t = (item.get("title") or "").lower()
        if a == artist_lower and t == title_lower:
            return {"deezer_id": str(item["id"]), "api_title": item.get("title", "")}

    return None


# ---------------------------------------------------------------------------
# Universal artist validation helpers (Phase 14 — Artist ID Registry)
# ---------------------------------------------------------------------------

def _match_album_title(lib_title: str, api_title: str) -> float:
    """
    Score how well a lib_album title matches an API catalog title.
    Normalizes both sides via norm() + _strip_title_suffixes() before comparing.
    Returns 1.0 for exact normalized match; SequenceMatcher ratio otherwise.
    Threshold for a 'match' in callers is ≥ 0.82.
    """
    from app.clients.music_client import norm
    a = norm(_strip_title_suffixes(lib_title))
    b = norm(_strip_title_suffixes(api_title))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _name_similarity_bonus(norm_target: str, norm_candidate: str) -> int:
    """Return a name similarity score bonus for use in _validate_artist scoring."""
    if norm_target == norm_candidate:
        return 1000
    if norm_target in norm_candidate or norm_candidate in norm_target:
        return 500
    # Partial match: first half of one appears in the other (avoids noise on short names)
    half = len(norm_target) // 2
    if half > 2 and norm_target[:half] in norm_candidate:
        return 300
    return 0


def _validate_artist(artist_name: str, lib_album_titles: list[str], source: str) -> dict | None:
    """
    Validate artist identity by scoring album catalog overlap against the user's library.

    Same logic applied across all sources:
      1. Search the API for artist name → up to 5 candidates
      2. For each candidate (top 3): fetch album catalog + count title overlaps
      3. score = name_similarity_bonus + (overlap_count × 300)
      4. Confidence: 0 overlaps = 70 (name-only), 1–2 = 85 (medium), 3+ = 95 (high)
      5. If no name similarity at all → reject (returns None)

    Returns {artist_id, confidence, album_catalog} or None.
      - artist_id: the source-specific ID to store (itunes_artist_id / deezer_artist_id / etc.)
      - confidence: 70 / 85 / 95
      - album_catalog: list of {id, title} dicts from the API (kept in memory for album matching)
    """
    from app.clients.music_client import (
        norm,
        search_artist_candidates_itunes,
        get_artist_albums_itunes,
        search_artist_candidates_deezer,
        get_artist_albums_deezer,
    )

    norm_name = norm(artist_name)

    # --- Fetch candidates per source ---
    if source == "itunes":
        raw_candidates = search_artist_candidates_itunes(artist_name)
        # [{name, id}]
        candidates = [{"name": c["name"], "id": c["id"]} for c in raw_candidates]
    elif source == "deezer":
        raw_candidates = search_artist_candidates_deezer(artist_name)
        candidates = [{"name": c["name"], "id": c["id"]} for c in raw_candidates]
    elif source == "lastfm":
        from app.clients.last_fm_client import (
            search_artist_candidates_lastfm,
            get_artist_top_albums_lastfm,
        )
        raw_candidates = search_artist_candidates_lastfm(artist_name)
        candidates = [{"name": c["name"], "id": c.get("mbid", ""), "mbid": c.get("mbid", "")}
                      for c in raw_candidates]
    else:
        logger.debug("_validate_artist: unsupported source '%s'", source)
        return None

    if not candidates:
        logger.debug("_validate_artist: no candidates for '%s' on %s", artist_name, source)
        return None

    # --- Score each candidate ---
    best: dict | None = None
    best_score = -1

    for candidate in candidates[:3]:
        name_bonus = _name_similarity_bonus(norm_name, norm(candidate["name"]))
        if name_bonus == 0:
            continue  # skip candidates with no name similarity at all

        # Fetch album catalog for this candidate
        if source == "itunes":
            catalog = get_artist_albums_itunes(candidate["id"])
            catalog_titles = [c["title"] for c in catalog]
        elif source == "deezer":
            catalog = get_artist_albums_deezer(candidate["id"])
            catalog_titles = [c["title"] for c in catalog]
        elif source == "lastfm":
            mbid_or_name = candidate.get("mbid") or candidate["name"]
            use_mbid = bool(candidate.get("mbid"))
            catalog_titles = get_artist_top_albums_lastfm(mbid_or_name, use_mbid=use_mbid)
            catalog = [{"title": t} for t in catalog_titles]
        else:
            catalog = []
            catalog_titles = []

        # Count lib_album title overlaps against catalog
        overlap = 0
        for lib_title in lib_album_titles:
            for api_title in catalog_titles:
                if _match_album_title(lib_title, api_title) >= 0.82:
                    overlap += 1
                    break  # count each lib album once

        score = name_bonus + (overlap * 300)
        if score > best_score:
            best_score = score
            best = {
                "candidate": candidate,
                "catalog": catalog,
                "overlap": overlap,
                "name_bonus": name_bonus,
            }

    if best is None:
        logger.debug("_validate_artist: no name-similar candidates for '%s' on %s",
                     artist_name, source)
        return None

    # Confidence based on album overlap count
    overlap = best["overlap"]
    if overlap >= 3:
        confidence = 95
    elif overlap >= 1:
        confidence = 85
    else:
        confidence = 70  # name-only match

    artist_id = best["candidate"].get("id") or best["candidate"].get("mbid", "")
    if not artist_id:
        # Last.fm may not return an MBID for all artists — still usable at low confidence
        logger.debug("_validate_artist: '%s' on %s — no ID in candidate, skipping",
                     artist_name, source)
        return None

    logger.debug(
        "_validate_artist: '%s' on %s → id=%s overlap=%d confidence=%d",
        artist_name, source, artist_id, overlap, confidence,
    )
    return {
        "artist_id": artist_id,
        "confidence": confidence,
        "album_catalog": best["catalog"],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_library() -> dict:
    """
    Stage 1: Walk the active library backend → write lib_* tables.
    Routes to the correct backend (plex_reader or soulsync_reader) via get_library_reader().
    After sync, prunes lib_releases rows older than 180 days with is_owned=0.
    Returns {artist_count, album_count, track_count, sync_duration_s}.
    """
    from app.db import get_library_reader
    result = get_library_reader().sync_library()
    _prune_old_releases()
    return result


def _prune_old_releases() -> None:
    """Delete lib_releases rows older than 180 days that are not owned.
    Owned releases are kept indefinitely. Called after each library sync.
    """
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM lib_releases "
                "WHERE is_owned = 0 "
                "AND first_seen_at < datetime('now', '-180 days')"
            )
    except Exception as e:
        logger.warning("prune_old_releases failed (table may not exist yet): %s", e)


def _write_enrichment_meta(conn, source: str, entity_type: str, entity_id: str,
                           status: str, error_msg: str | None = None,
                           confidence: int | None = None) -> None:
    """Upsert a row into enrichment_meta. Silently ignores if table doesn't exist yet.
    For 'not_found' status, automatically sets retry_after = date('now', '+30 days') (S3-6)."""
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO enrichment_meta
                (source, entity_type, entity_id, status, enriched_at, error_msg, confidence,
                 retry_after)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?,
                    CASE WHEN ? = 'not_found' THEN date('now', '+30 days') ELSE NULL END)
            """,
            (source, entity_type, entity_id, status, error_msg, confidence, status),
        )
    except Exception as e:
        logger.debug("enrichment_meta write skipped: %s", e)


def enrich_library(batch_size: int = 50, stop_event: threading.Event | None = None,
                    on_progress: "callable | None" = None) -> dict:
    """
    Stage 2 — Primary ID Workers: artist-first confidence loop for iTunes + Deezer.

    Batches by artist (not album). For each artist:
      FAST PATH  — stored artist ID at confidence ≥ 85: skip validation, fetch catalog directly.
      VALIDATION — no stored ID: run _validate_artist() for iTunes + Deezer independently.
                   Both always run — writes both itunes_album_id AND deezer_id when found.
                   This fixes the BPM-blocked bug where iTunes hit → Deezer skipped → deezer_id NULL.

    Album matching uses pre-fetched catalog + _match_album_title() threshold ≥ 0.82.

    Resumable: skips artists where both sources are already 'found'/'not_found' for all albums.
    Returns {enriched, failed, skipped, remaining}.
    """
    enriched = 0
    failed = 0
    skipped = 0

    # Load artists that still have albums needing iTunes or Deezer IDs
    try:
        with _connect() as conn:
            artist_rows = conn.execute(
                """
                SELECT DISTINCT ar.id, ar.name,
                       ar.itunes_artist_id, ar.deezer_artist_id
                FROM lib_artists ar
                JOIN lib_albums la ON la.artist_id = ar.id
                WHERE la.removed_at IS NULL
                  AND (la.itunes_album_id IS NULL OR la.deezer_id IS NULL)
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_library: could not read lib_artists: %s", e)
        return {"enriched": 0, "failed": 0, "skipped": 0, "remaining": -1, "error": str(e)}

    if not artist_rows:
        logger.info("enrich_library: nothing to enrich — all albums have IDs")
        return {"enriched": 0, "failed": 0, "skipped": 0, "remaining": 0}

    # Pre-count pending albums for progress reporting
    try:
        with _connect() as conn:
            _total_pending = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE removed_at IS NULL"
                " AND (itunes_album_id IS NULL OR deezer_id IS NULL)"
            ).fetchone()[0]
    except Exception:
        _total_pending = len(artist_rows)

    for artist in artist_rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]

        # Load this artist's albums that still need IDs
        try:
            with _connect() as conn:
                album_rows = conn.execute(
                    """
                    SELECT id, title, local_title, itunes_album_id, deezer_id
                    FROM lib_albums
                    WHERE artist_id = ? AND removed_at IS NULL
                      AND (itunes_album_id IS NULL OR deezer_id IS NULL)
                    """,
                    (artist_id,),
                ).fetchall()
        except Exception as e:
            logger.warning("enrich_library: could not load albums for '%s': %s", artist_name, e)
            failed += 1
            continue

        if not album_rows:
            continue

        lib_titles = [_strip_title_suffixes(r["local_title"] or r["title"]) for r in album_rows]

        # --- iTunes: fast path or validation ---
        itunes_catalog: list[dict] = []
        itunes_artist_id = artist["itunes_artist_id"]

        if itunes_artist_id:
            # Fast path: stored ID — fetch catalog directly, skip validation
            from app.clients.music_client import get_artist_albums_itunes
            itunes_catalog = get_artist_albums_itunes(itunes_artist_id)
            logger.debug("enrich_library: iTunes fast path for '%s' (id=%s, %d albums)",
                         artist_name, itunes_artist_id, len(itunes_catalog))
        else:
            # Validation path: search + album overlap scoring
            val = _validate_artist(artist_name, lib_titles, "itunes")
            if val:
                itunes_artist_id = val["artist_id"]
                itunes_catalog = val["album_catalog"]
                try:
                    with _connect() as conn:
                        conn.execute(
                            """
                            UPDATE lib_artists
                            SET itunes_artist_id = ?, match_confidence = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND itunes_artist_id IS NULL
                            """,
                            (itunes_artist_id, val["confidence"], artist_id),
                        )
                        _write_enrichment_meta(conn, "itunes_artist", "artist", artist_id,
                                               "found", confidence=val["confidence"])
                    logger.debug(
                        "enrich_library: iTunes validated '%s' → id=%s conf=%d",
                        artist_name, itunes_artist_id, val["confidence"],
                    )
                except Exception as e:
                    logger.warning("enrich_library: iTunes artist write failed for '%s': %s",
                                   artist_name, e)
            else:
                try:
                    with _connect() as conn:
                        _write_enrichment_meta(conn, "itunes_artist", "artist", artist_id,
                                               "not_found")
                except Exception:
                    pass

        # --- Deezer: fast path or validation ---
        deezer_catalog: list[dict] = []
        deezer_artist_id = artist["deezer_artist_id"]

        if deezer_artist_id:
            # Fast path: stored ID
            from app.clients.music_client import get_artist_albums_deezer
            deezer_catalog = get_artist_albums_deezer(deezer_artist_id)
            logger.debug("enrich_library: Deezer fast path for '%s' (id=%s, %d albums)",
                         artist_name, deezer_artist_id, len(deezer_catalog))
        else:
            val = _validate_artist(artist_name, lib_titles, "deezer")
            if val:
                deezer_artist_id = val["artist_id"]
                deezer_catalog = val["album_catalog"]
                try:
                    with _connect() as conn:
                        conn.execute(
                            """
                            UPDATE lib_artists
                            SET deezer_artist_id = ?, match_confidence = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND deezer_artist_id IS NULL
                            """,
                            (deezer_artist_id, val["confidence"], artist_id),
                        )
                        _write_enrichment_meta(conn, "deezer_artist", "artist", artist_id,
                                               "found", confidence=val["confidence"])
                except Exception as e:
                    logger.warning("enrich_library: Deezer artist write failed for '%s': %s",
                                   artist_name, e)
            else:
                try:
                    with _connect() as conn:
                        _write_enrichment_meta(conn, "deezer_artist", "artist", artist_id,
                                               "not_found")
                except Exception:
                    pass

        # --- Album matching against pre-fetched catalogs ---
        itunes_titles = {c["title"]: c["id"] for c in itunes_catalog if c.get("title")}
        deezer_titles = {c["title"]: c.get("id", "") for c in deezer_catalog if c.get("title")}

        for album in album_rows:
            album_id = album["id"]
            album_title = _strip_title_suffixes(album["local_title"] or album["title"])
            album_enriched = False

            # iTunes album match
            if album["itunes_album_id"] is None and itunes_titles:
                best_itunes = max(
                    ((t, _match_album_title(album_title, t)) for t in itunes_titles),
                    key=lambda x: x[1],
                    default=(None, 0.0),
                )
                if best_itunes[1] >= 0.82:
                    matched_id = itunes_titles[best_itunes[0]]
                    try:
                        with _connect() as conn:
                            conn.execute(
                                """
                                UPDATE lib_albums
                                SET itunes_album_id = ?,
                                    api_title = ?,
                                    match_confidence = 90,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ? AND itunes_album_id IS NULL
                                """,
                                (matched_id, best_itunes[0], album_id),
                            )
                            _write_enrichment_meta(conn, "itunes", "album", album_id,
                                                   "found", confidence=90)
                        album_enriched = True
                        logger.debug(
                            "enrich_library: iTunes album hit '%s / %s' → id=%s (score=%.2f)",
                            artist_name, album_title, matched_id, best_itunes[1],
                        )
                    except Exception as e:
                        logger.warning("enrich_library: iTunes album write failed '%s / %s': %s",
                                       artist_name, album_title, e)
                        failed += 1
                        if on_progress:
                            on_progress(enriched, skipped, failed, _total_pending)
                        continue
                else:
                    try:
                        with _connect() as conn:
                            _write_enrichment_meta(conn, "itunes", "album", album_id,
                                                   "not_found", confidence=0)
                    except Exception:
                        pass

            # Deezer album match (always runs — not a fallback)
            if album["deezer_id"] is None and deezer_titles:
                best_deezer = max(
                    ((t, _match_album_title(album_title, t)) for t in deezer_titles),
                    key=lambda x: x[1],
                    default=(None, 0.0),
                )
                if best_deezer[1] >= 0.82:
                    matched_id = deezer_titles[best_deezer[0]]
                    try:
                        with _connect() as conn:
                            conn.execute(
                                """
                                UPDATE lib_albums
                                SET deezer_id = ?,
                                    match_confidence = CASE
                                        WHEN itunes_album_id IS NOT NULL THEN 95
                                        ELSE 75
                                    END,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ? AND deezer_id IS NULL
                                """,
                                (matched_id, album_id),
                            )
                            conf = 95 if album["itunes_album_id"] or album_enriched else 75
                            _write_enrichment_meta(conn, "deezer", "album", album_id,
                                                   "found", confidence=conf)
                        album_enriched = True
                        logger.debug(
                            "enrich_library: Deezer album hit '%s / %s' → id=%s (score=%.2f)",
                            artist_name, album_title, matched_id, best_deezer[1],
                        )
                    except Exception as e:
                        logger.warning("enrich_library: Deezer album write failed '%s / %s': %s",
                                       artist_name, album_title, e)
                        failed += 1
                        if on_progress:
                            on_progress(enriched, skipped, failed, _total_pending)
                        continue
                else:
                    try:
                        with _connect() as conn:
                            _write_enrichment_meta(conn, "deezer", "album", album_id,
                                                   "not_found", confidence=0)
                    except Exception:
                        pass

            # Album with no artist catalog match at all → flag for review
            if not album_enriched and not itunes_titles and not deezer_titles:
                try:
                    with _connect() as conn:
                        conn.execute(
                            """
                            UPDATE lib_albums
                            SET match_confidence = 0, needs_verification = 1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (album_id,),
                        )
                except Exception:
                    pass
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, _total_pending)
            elif album_enriched:
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, _total_pending)
            else:
                # Artist found on APIs but no album title hit match threshold — count as not_found
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, _total_pending)

    # Count remaining unenriched albums
    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE itunes_album_id IS NULL AND deezer_id IS NULL"
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info(
        "enrich_library: enriched=%d, skipped=%d, failed=%d, remaining=%d",
        enriched, skipped, failed, remaining,
    )
    return {"enriched": enriched, "failed": failed, "skipped": skipped, "remaining": remaining}


def enrich_artist_ids_spotify(batch_size: int = 20, stop_event: threading.Event | None = None,
                               on_progress: "callable | None" = None) -> dict:
    """
    Stage 2 — Spotify ID Worker: validate + store spotify_artist_id only.
    No rich data (genres, popularity) — those belong in Stage 3 (enrich_genres_spotify).
    Optional: gracefully skips if SPOTIFY_CLIENT_ID/SECRET not configured.
    Returns {enriched, skipped, failed, remaining}.
    """
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1,
                "error": "Spotify credentials not configured"}

    try:
        import spotipy  # type: ignore[import]
        from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore[import]
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
    except Exception as e:
        logger.error("enrich_artist_ids_spotify: Spotify client init failed: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name FROM lib_artists
                WHERE spotify_artist_id IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_artist_ids_spotify: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]

        try:
            from app.clients.music_client import norm

            try:
                with _connect() as conn:
                    lib_titles = [
                        _strip_title_suffixes(r["local_title"] or r["title"])
                        for r in conn.execute(
                            "SELECT title, local_title FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL",
                            (artist_id,),
                        ).fetchall()
                    ]
            except Exception:
                lib_titles = []

            rate_limiter.acquire("spotify")
            results = sp.search(q=f'artist:"{artist_name}"', type="artist", limit=5)
            items = results.get("artists", {}).get("items", [])

            if not items:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                           "not_found", confidence=0)
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                continue

            norm_name = norm(artist_name)
            best_candidate = None
            best_score = -1
            best_conf = 0

            for candidate in items[:3]:
                name_bonus = _name_similarity_bonus(norm_name, norm(candidate["name"]))
                if name_bonus == 0:
                    continue
                rate_limiter.acquire("spotify")
                albums_resp = sp.artist_albums(
                    candidate["id"], include_groups="album,single", limit=50
                )
                catalog_titles = [a["name"] for a in albums_resp.get("items", [])]
                overlap = sum(
                    1 for lt in lib_titles
                    if any(_match_album_title(lt, ct) >= 0.82 for ct in catalog_titles)
                )
                score = name_bonus + (overlap * 300)
                if score > best_score:
                    best_score = score
                    best_candidate = candidate
                    best_conf = 95 if overlap >= 3 else (85 if overlap >= 1 else 70)

            if best_candidate is None:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                           "not_found", confidence=0)
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                continue

            needs_verification = 1 if best_conf < 85 else 0
            with _connect() as conn:
                conn.execute(
                    """
                    UPDATE lib_artists
                    SET spotify_artist_id = ?,
                        needs_verification = CASE WHEN ? = 1 THEN 1 ELSE needs_verification END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND spotify_artist_id IS NULL
                    """,
                    (best_candidate["id"], needs_verification, artist_id),
                )
                _write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                       "found", confidence=best_conf)
            enriched += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
            logger.debug("enrich_artist_ids_spotify: '%s' → id=%s conf=%d",
                         artist_name, best_candidate["id"], best_conf)

        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("enrich_artist_ids_spotify: rate limit hit on '%s' — stopping", artist_name)
                break
            logger.warning("enrich_artist_ids_spotify: failed for '%s': %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                           "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE spotify_artist_id IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_artist_ids_spotify: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def enrich_genres_spotify(batch_size: int = 20, stop_event: threading.Event | None = None,
                           on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Spotify genres + popularity worker.
    Requires: spotify_artist_id stored by enrich_artist_ids_spotify() (Stage 2).
    Fetches: genres_json, popularity, appears_on albums, raw cache.
    Optional: gracefully skips if SPOTIFY_CLIENT_ID/SECRET not configured.
    Returns {enriched, skipped, failed, remaining}.
    """
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1,
                "error": "Spotify credentials not configured"}

    try:
        import spotipy  # type: ignore[import]
        from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore[import]
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
    except Exception as e:
        logger.error("enrich_genres_spotify: Spotify client init failed: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    import json

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, spotify_artist_id FROM lib_artists
                WHERE spotify_artist_id IS NOT NULL
                  AND genres_json IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_genres'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_genres_spotify: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]
        spotify_artist_id = artist["spotify_artist_id"]

        try:
            rate_limiter.acquire("spotify")
            artist_data = sp.artist(spotify_artist_id)

            rate_limiter.acquire("spotify")
            appears_on_data = sp.artist_albums(
                spotify_artist_id, include_groups="appears_on", limit=20
            )

            with _connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO spotify_raw_cache
                        (query_type, entity_id, entity_name, raw_json, fetched_at)
                    VALUES ('artist', ?, ?, ?, datetime('now'))
                    """,
                    (spotify_artist_id, artist_name, json.dumps(artist_data)),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO spotify_raw_cache
                        (query_type, entity_id, entity_name, raw_json, fetched_at)
                    VALUES ('appears_on', ?, ?, ?, datetime('now'))
                    """,
                    (spotify_artist_id, artist_name, json.dumps(appears_on_data)),
                )
                genres_json = json.dumps(artist_data.get("genres", []))
                popularity = artist_data.get("popularity")
                conn.execute(
                    """
                    UPDATE lib_artists
                    SET genres_json = ?,
                        popularity = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (genres_json, popularity, artist_id),
                )
                _write_enrichment_meta(conn, "spotify_genres", "artist", artist_id, "found")
            enriched += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
            logger.debug("enrich_genres_spotify: '%s' → genres=%s popularity=%s",
                         artist_name, artist_data.get("genres", [])[:3], popularity)

        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("enrich_genres_spotify: rate limit hit on '%s' — stopping", artist_name)
                break
            logger.warning("enrich_genres_spotify: failed for '%s': %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "spotify_genres", "artist", artist_id,
                                           "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE spotify_artist_id IS NOT NULL
                  AND genres_json IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_genres'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_genres_spotify: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def enrich_spotify(batch_size: int = 20) -> dict:
    """
    Thin wrapper — runs Stage 2 (ID resolution) then Stage 3 (genres/popularity).
    For direct per-stage invocation use enrich_artist_ids_spotify() or enrich_genres_spotify().
    Returns combined result dict.
    """
    s2 = enrich_artist_ids_spotify(batch_size)
    s3 = enrich_genres_spotify(batch_size)
    return {
        "enriched": s2.get("enriched", 0) + s3.get("enriched", 0),
        "skipped": s2.get("skipped", 0) + s3.get("skipped", 0),
        "failed": s2.get("failed", 0) + s3.get("failed", 0),
        "remaining": s3.get("remaining", -1),
        "stage2": s2,
        "stage3": s3,
    }


def get_spotify_status() -> dict:
    """Return Spotify enrichment status for the Settings UI."""
    try:
        with _connect() as conn:
            total_row = conn.execute("SELECT COUNT(*) FROM lib_artists").fetchone()
            total = total_row[0] if total_row else 0

            enriched_row = conn.execute(
                "SELECT COUNT(*) FROM lib_artists WHERE spotify_artist_id IS NOT NULL"
            ).fetchone()
            enriched = enriched_row[0] if enriched_row else 0
    except Exception:
        total = 0
        enriched = 0

    last_run = rythmx_store.get_setting("spotify_enrich_last_run")
    return {
        "enriched_artists": enriched,
        "total_artists": total,
        "last_run": last_run,
        "spotify_available": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
    }


def _normalize_lastfm_tags(raw_tags: list) -> list[str]:
    """
    Normalize raw Last.fm tags to canonical genre labels (Stage 3 S3-4 inline normalization).
    raw_tags format: [[tag_name, tag_count], ...] or [tag_name, ...]
    Returns up to 5 matched canonical labels (deduplicated, in score order).
    Unknown tags are discarded — only whitelist matches are kept.
    """
    canonical: list[str] = []
    seen: set[str] = set()
    for tag_pair in raw_tags:
        tag_name = (tag_pair[0] if isinstance(tag_pair, (list, tuple)) else str(tag_pair)).lower().strip()
        label = config.LASTFM_GENRE_WHITELIST.get(tag_name)
        if label and label not in seen:
            canonical.append(label)
            seen.add(label)
            if len(canonical) >= 5:
                break
    return canonical


def enrich_artist_ids_lastfm(batch_size: int = 50, stop_event: threading.Event | None = None,
                              on_progress: "callable | None" = None) -> dict:
    """
    Stage 2 — Last.fm MBID Worker: validate + store lastfm_mbid only.
    No tag fetching — tags belong in Stage 3 (enrich_tags_lastfm).
    Returns {enriched, skipped, failed, remaining}.
    """
    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name FROM lib_artists
                WHERE lastfm_mbid IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_artist_ids_lastfm: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]

        try:
            try:
                with _connect() as conn:
                    lib_titles = [
                        _strip_title_suffixes(r["local_title"] or r["title"])
                        for r in conn.execute(
                            "SELECT title, local_title FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL",
                            (artist_id,),
                        ).fetchall()
                    ]
            except Exception:
                lib_titles = []

            val = _validate_artist(artist_name, lib_titles, "lastfm")
            if val and val["confidence"] >= 70:
                mbid = val["artist_id"]
                needs_verification = 1 if val["confidence"] < 85 else 0
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_artists
                        SET lastfm_mbid = ?,
                            needs_verification = CASE WHEN ? = 1 THEN 1 ELSE needs_verification END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND lastfm_mbid IS NULL
                        """,
                        (mbid, needs_verification, artist_id),
                    )
                    _write_enrichment_meta(conn, "lastfm_id", "artist", artist_id,
                                           "found", confidence=val["confidence"])
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                logger.debug("enrich_artist_ids_lastfm: '%s' → mbid=%s conf=%d",
                             artist_name, mbid, val["confidence"])
            else:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_id", "artist", artist_id,
                                           "not_found", confidence=0)
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_artist_ids_lastfm: failed for '%s': %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_id", "artist", artist_id,
                                           "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE lastfm_mbid IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_artist_ids_lastfm: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def enrich_tags_lastfm(batch_size: int = 50, stop_event: threading.Event | None = None,
                        on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Last.fm tags worker (artist + album).
    Requires: lastfm_mbid stored by enrich_artist_ids_lastfm() (Stage 2).
    Artists without lastfm_mbid are still attempted by name (graceful degradation).
    Normalizes tags inline: top 5, whitelist-filtered, canonical labels (S3-4).
    Returns {enriched_artists, enriched_albums, skipped, failed, remaining_artists, remaining_albums}.
    """
    from app.clients.last_fm_client import get_artist_tags, get_album_tags
    import json

    enriched_artists = 0
    enriched_albums = 0
    skipped = 0
    failed = 0

    # --- Artist pass ---
    try:
        with _connect() as conn:
            artist_rows = conn.execute(
                """
                SELECT id, name, lastfm_mbid FROM lib_artists
                WHERE lastfm_tags_json IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_tags'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_tags_lastfm: could not read lib_artists: %s", e)
        return {"enriched_artists": 0, "enriched_albums": 0, "skipped": 0,
                "failed": 0, "remaining_artists": -1, "remaining_albums": -1, "error": str(e)}

    for artist in artist_rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]

        try:
            raw_tags = get_artist_tags(artist_name)
            canonical = _normalize_lastfm_tags(raw_tags)
            tags_json = json.dumps(canonical)
            status = "found" if canonical else "not_found"
            with _connect() as conn:
                conn.execute(
                    "UPDATE lib_artists SET lastfm_tags_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (tags_json, artist_id),
                )
                _write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, status)
            enriched_artists += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows))
            logger.debug("enrich_tags_lastfm artist '%s': %s", artist_name, canonical)
        except Exception as e:
            logger.warning("enrich_tags_lastfm: artist '%s' failed: %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_tags", "artist", artist_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows))

    # --- Album pass ---
    try:
        with _connect() as conn:
            album_rows = conn.execute(
                """
                SELECT a.id, a.title, a.artist_id,
                       ar.name AS artist_name, ar.lastfm_tags_json AS artist_tags
                FROM lib_albums a
                JOIN lib_artists ar ON ar.id = a.artist_id
                WHERE a.lastfm_tags_json IS NULL
                  AND a.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'lastfm_tags'
                        AND (status = 'found'
                             OR status = 'fallback'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_tags_lastfm: could not read lib_albums: %s", e)
        album_rows = []

    for album in album_rows:
        album_id = album["id"]
        album_title = album["title"]
        artist_name = album["artist_name"]
        artist_tags_json = album["artist_tags"]
        try:
            raw_tags = get_album_tags(artist_name, album_title)
            if raw_tags:
                canonical = _normalize_lastfm_tags(raw_tags)
                tags_json = json.dumps(canonical)
                status = "found"
            else:
                # Fallback: use parent artist's already-normalized tags
                tags_json = artist_tags_json or json.dumps([])
                status = "fallback"
            with _connect() as conn:
                conn.execute(
                    "UPDATE lib_albums SET lastfm_tags_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (tags_json, album_id),
                )
                _write_enrichment_meta(conn, "lastfm_tags", "album", album_id, status)
            enriched_albums += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows) + len(album_rows))
            logger.debug("enrich_tags_lastfm album '%s / %s': status=%s",
                         artist_name, album_title, status)
        except Exception as e:
            logger.warning("enrich_tags_lastfm: album '%s / %s' failed: %s",
                           artist_name, album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_tags", "album", album_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched_artists + enriched_albums, skipped, failed, len(artist_rows) + len(album_rows))

    # Count remaining
    try:
        with _connect() as conn:
            rem_artists = conn.execute(
                """SELECT COUNT(*) FROM lib_artists WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='artist' AND source='lastfm_tags'
                                    AND (status = 'found'
                                         OR (status = 'not_found'
                                             AND (retry_after IS NULL
                                                  OR retry_after > date('now')))))"""
            ).fetchone()[0]
            rem_albums = conn.execute(
                """SELECT COUNT(*) FROM lib_albums WHERE lastfm_tags_json IS NULL
                   AND id NOT IN (SELECT entity_id FROM enrichment_meta
                                  WHERE entity_type='album' AND source='lastfm_tags'
                                    AND (status IN ('found', 'fallback')
                                         OR (status = 'not_found'
                                             AND (retry_after IS NULL
                                                  OR retry_after > date('now')))))"""
            ).fetchone()[0]
    except Exception:
        rem_artists = rem_albums = -1

    logger.info("enrich_tags_lastfm: artists=%d albums=%d failed=%d remaining=%d/%d",
                enriched_artists, enriched_albums, failed, rem_artists, rem_albums)
    return {
        "enriched_artists": enriched_artists,
        "enriched_albums": enriched_albums,
        "skipped": skipped,
        "failed": failed,
        "remaining_artists": rem_artists,
        "remaining_albums": rem_albums,
    }


def enrich_lastfm_tags(batch_size: int = 50) -> dict:
    """
    Thin wrapper — runs Stage 2 (MBID resolution) then Stage 3 (tags + normalization).
    For direct per-stage invocation use enrich_artist_ids_lastfm() or enrich_tags_lastfm().
    Returns combined result dict.
    """
    s2 = enrich_artist_ids_lastfm(batch_size)
    s3 = enrich_tags_lastfm(batch_size)
    return {
        "enriched_artists": s3.get("enriched_artists", 0),
        "enriched_albums": s3.get("enriched_albums", 0),
        "skipped": s2.get("skipped", 0) + s3.get("skipped", 0),
        "failed": s2.get("failed", 0) + s3.get("failed", 0),
        "remaining_artists": s3.get("remaining_artists", -1),
        "remaining_albums": s3.get("remaining_albums", -1),
        "stage2": s2,
        "stage3": s3,
    }


def get_lastfm_tags_status() -> dict:
    """Return Last.fm tag enrichment status for the Settings UI."""
    try:
        with _connect() as conn:
            total_artists = conn.execute("SELECT COUNT(*) FROM lib_artists").fetchone()[0]
            enriched_artists = conn.execute(
                "SELECT COUNT(*) FROM lib_artists WHERE lastfm_tags_json IS NOT NULL"
            ).fetchone()[0]
            total_albums = conn.execute("SELECT COUNT(*) FROM lib_albums").fetchone()[0]
            enriched_albums = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE lastfm_tags_json IS NOT NULL"
            ).fetchone()[0]
    except Exception:
        total_artists = enriched_artists = total_albums = enriched_albums = 0

    last_run = rythmx_store.get_setting("lastfm_tags_last_run")
    return {
        "enriched_artists": enriched_artists,
        "total_artists": total_artists,
        "enriched_albums": enriched_albums,
        "total_albums": total_albums,
        "last_run": last_run,
        "lastfm_available": bool(config.LASTFM_API_KEY),
    }


def get_status() -> dict:
    """
    Return combined sync + enrich status for the Settings UI.
    Always safe to call — returns sane defaults if tables don't exist yet.
    """
    last_synced = rythmx_store.get_setting("library_last_synced")
    backend = rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM

    try:
        with _connect() as conn:
            track_row = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()
            track_count = track_row[0] if track_row else 0

            album_row = conn.execute("SELECT COUNT(*) FROM lib_albums").fetchone()
            total_albums = album_row[0] if album_row else 0

            enriched_row = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE itunes_album_id IS NOT NULL OR deezer_id IS NOT NULL"
            ).fetchone()
            enriched_albums = enriched_row[0] if enriched_row else 0
    except Exception:
        track_count = 0
        total_albums = 0
        enriched_albums = 0

    enrich_pct = round(enriched_albums / total_albums * 100) if total_albums else 0

    return {
        "synced": track_count > 0,
        "last_synced": last_synced,
        "backend": backend,
        "track_count": track_count,
        "total_albums": total_albums,
        "enriched_albums": enriched_albums,
        "enrich_pct": enrich_pct,
    }


# ---------------------------------------------------------------------------
# Stage 3 — Rich data workers (S3-1 through S3-5)
# ---------------------------------------------------------------------------


def enrich_itunes_rich(batch_size: int = 50, stop_event: threading.Event | None = None,
                        on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — iTunes rich data worker: genre + release_date per album.
    Requires: itunes_album_id (from Stage 2 enrich_library).
    Writes: lib_albums.genre (COALESCE — won't overwrite existing), lib_albums.release_date.
    Returns {enriched, skipped, failed, remaining}.
    """
    from app.clients.music_client import get_album_itunes_rich

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, itunes_album_id, title FROM lib_albums
                WHERE itunes_album_id IS NOT NULL
                  AND (genre IS NULL OR release_date IS NULL)
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'itunes_rich'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_itunes_rich: could not read lib_albums: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for album in rows:
        if stop_event and stop_event.is_set():
            break
        album_id = album["id"]
        itunes_album_id = album["itunes_album_id"]
        album_title = album["title"]

        try:
            result = get_album_itunes_rich(itunes_album_id)
            if result and (result.get("genre") or result.get("release_date")):
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_albums
                        SET genre = COALESCE(genre, ?),
                            release_date = COALESCE(release_date, ?),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (result.get("genre") or None, result.get("release_date") or None, album_id),
                    )
                    _write_enrichment_meta(conn, "itunes_rich", "album", album_id, "found")
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                logger.debug("enrich_itunes_rich: '%s' → genre=%s release=%s",
                             album_title, result.get("genre"), result.get("release_date"))
            else:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "itunes_rich", "album", album_id, "not_found")
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_itunes_rich: album '%s' failed: %s", album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "itunes_rich", "album", album_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_albums
                WHERE itunes_album_id IS NOT NULL
                  AND (genre IS NULL OR release_date IS NULL)
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'itunes_rich'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_itunes_rich: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def enrich_deezer_release(batch_size: int = 50, stop_event: threading.Event | None = None,
                           on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Deezer release data worker: record_type + thumb_url (CDN art URL).
    Requires: deezer_id (from Stage 2 enrich_library).
    Writes: lib_albums.record_type, lib_albums.thumb_url (CDN URL — persists when Plex offline).
    COALESCE on both columns — won't overwrite existing values.
    Returns {enriched, skipped, failed, remaining}.
    """
    from app.clients.music_client import get_deezer_album_info

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, deezer_id, title FROM lib_albums
                WHERE deezer_id IS NOT NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'deezer_rich'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_deezer_release: could not read lib_albums: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for album in rows:
        if stop_event and stop_event.is_set():
            break
        album_id = album["id"]
        deezer_id = album["deezer_id"]
        album_title = album["title"]

        try:
            result = get_deezer_album_info(deezer_id)
            if result:
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_albums
                        SET record_type = COALESCE(record_type, ?),
                            thumb_url = COALESCE(thumb_url, ?),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (result.get("record_type") or None,
                         result.get("thumb_url") or None,
                         album_id),
                    )
                    _write_enrichment_meta(conn, "deezer_rich", "album", album_id, "found")
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                logger.debug("enrich_deezer_release: '%s' → type=%s thumb=%s",
                             album_title, result.get("record_type"), bool(result.get("thumb_url")))
            else:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_rich", "album", album_id, "not_found")
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_deezer_release: album '%s' failed: %s", album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_rich", "album", album_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_albums
                WHERE deezer_id IS NOT NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'deezer_rich'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_deezer_release: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def enrich_stats_lastfm(batch_size: int = 50, stop_event: threading.Event | None = None,
                         on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Last.fm listener/play count worker.
    Requires: lastfm_mbid (from Stage 2 enrich_artist_ids_lastfm).
    Writes: lib_artists.listener_count, lib_artists.global_play_count.
    Returns {enriched, skipped, failed, remaining}.
    """
    from app.clients.last_fm_client import get_artist_info_lastfm

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, lastfm_mbid FROM lib_artists
                WHERE lastfm_mbid IS NOT NULL
                  AND (listener_count IS NULL OR global_play_count IS NULL)
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_stats'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_stats_lastfm: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]
        mbid = artist["lastfm_mbid"]

        try:
            stats = get_artist_info_lastfm(mbid=mbid, name=artist_name)
            if stats:
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_artists
                        SET listener_count = ?,
                            global_play_count = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (stats["listeners"], stats["playcount"], artist_id),
                    )
                    _write_enrichment_meta(conn, "lastfm_stats", "artist", artist_id, "found")
                enriched += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                logger.debug("enrich_stats_lastfm: '%s' → listeners=%d plays=%d",
                             artist_name, stats["listeners"], stats["playcount"])
            else:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_stats", "artist", artist_id, "not_found")
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_stats_lastfm: artist '%s' failed: %s", artist_name, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "lastfm_stats", "artist", artist_id, "error",
                                           error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE lastfm_mbid IS NOT NULL
                  AND (listener_count IS NULL OR global_play_count IS NULL)
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'lastfm_stats'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_stats_lastfm: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


# ---------------------------------------------------------------------------
# Deezer BPM enrichment
# ---------------------------------------------------------------------------

_DEEZER_ALBUM_URL = "https://api.deezer.com/album/{album_id}/tracks"
_DEEZER_TRACK_URL = "https://api.deezer.com/track/{track_id}"


def _deezer_rate_limited_get(url: str) -> dict | None:
    """Single rate-limited GET to Deezer via shared DomainRateLimiter. Returns parsed JSON or None."""
    import requests

    rate_limiter.acquire("deezer")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 429:
            rate_limiter.record_429("deezer")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("deezer")
        return resp.json()
    except Exception as e:
        logger.debug("Deezer request failed for %s: %s", url, e)
        return None


def _fetch_deezer_album_tracks(deezer_album_id: str) -> list[dict]:
    """
    Fetch BPM for all tracks in a Deezer album.

    Two-pass: first GET /album/{id}/tracks for track IDs, then GET /track/{id}
    per track for BPM (bpm is not included in the album tracks list response).

    Returns list of {title, bpm} dicts. Empty on error or no tracks.
    """
    # Pass 1: get track IDs from album endpoint
    data = _deezer_rate_limited_get(_DEEZER_ALBUM_URL.format(album_id=deezer_album_id))
    if not data:
        return []
    track_stubs = data.get("data", [])
    if not track_stubs:
        return []

    # Pass 2: fetch each track individually to get BPM
    results = []
    for stub in track_stubs:
        track_id = stub.get("id")
        title = stub.get("title", "")
        if not track_id:
            continue
        track_data = _deezer_rate_limited_get(_DEEZER_TRACK_URL.format(track_id=track_id))
        if not track_data:
            continue
        bpm = float(track_data.get("bpm", 0) or 0)
        if bpm > 0:
            results.append({"title": title, "bpm": bpm})

    return results


def enrich_deezer_bpm(batch_size: int = 30, stop_event: threading.Event | None = None,
                       on_progress: "callable | None" = None) -> dict:
    """
    Deezer BPM enrichment pass.

    For each lib_album with a deezer_id, fetch the Deezer track list and write
    bpm → lib_tracks.tempo using exact title match (title_lower).

    Only processes albums not already in enrichment_meta(source='deezer_bpm').
    Resumable — interrupted runs pick up where they left off.

    Returns {enriched_tracks, enriched_albums, failed, skipped, remaining}.
    """
    import json

    enriched_tracks = 0
    enriched_albums = 0
    failed = 0
    skipped = 0

    # Load albums that have a deezer_id but haven't been BPM-enriched yet
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT la.id, la.deezer_id, ar.name AS artist_name, la.title
                FROM lib_albums la
                JOIN lib_artists ar ON la.artist_id = ar.id
                WHERE la.deezer_id IS NOT NULL
                  AND la.id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'album' AND source = 'deezer_bpm'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_deezer_bpm: could not read lib_albums: %s", e)
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": -1, "error": str(e)}

    if not rows:
        logger.info("enrich_deezer_bpm: nothing to enrich")
        return {"enriched_tracks": 0, "enriched_albums": 0,
                "failed": 0, "skipped": 0, "remaining": 0}

    for album in rows:
        if stop_event and stop_event.is_set():
            break
        album_id = album["id"]
        deezer_album_id = album["deezer_id"]
        artist_name = album["artist_name"]
        album_title = album["title"]

        deezer_tracks = _fetch_deezer_album_tracks(deezer_album_id)

        if not deezer_tracks:
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_bpm", "album", album_id, "not_found")
                skipped += 1
                if on_progress:
                    on_progress(enriched_albums, skipped, failed, len(rows))
            except Exception:
                pass
            continue

        # Build lookup: title_lower → bpm
        bpm_map = {t["title"].lower(): t["bpm"] for t in deezer_tracks}

        try:
            with _connect() as conn:
                # Match lib_tracks for this album by title_lower
                lib_tracks = conn.execute(
                    "SELECT id, title_lower FROM lib_tracks WHERE album_id = ?",
                    (album_id,),
                ).fetchall()

                updated = 0
                for track in lib_tracks:
                    bpm = bpm_map.get(track["title_lower"])
                    if bpm:
                        conn.execute(
                            "UPDATE lib_tracks SET tempo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (bpm, track["id"]),
                        )
                        updated += 1

                _write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                       "found" if updated > 0 else "not_found")

            enriched_tracks += updated
            enriched_albums += 1
            if on_progress:
                on_progress(enriched_albums, skipped, failed, len(rows))
            logger.debug(
                "enrich_deezer_bpm: '%s / %s' → %d tracks updated",
                artist_name, album_title, updated,
            )
        except Exception as e:
            logger.warning("enrich_deezer_bpm: failed for '%s / %s': %s",
                           artist_name, album_title, e)
            try:
                with _connect() as conn:
                    _write_enrichment_meta(conn, "deezer_bpm", "album", album_id,
                                           "error", error_msg=str(e)[:200])
            except Exception:
                pass
            failed += 1
            if on_progress:
                on_progress(enriched_albums, skipped, failed, len(rows))

    logger.info(
        "enrich_deezer_bpm: enriched_tracks=%d enriched_albums=%d failed=%d skipped=%d",
        enriched_tracks, enriched_albums, failed, skipped,
    )
    return {
        "enriched_tracks": enriched_tracks,
        "enriched_albums": enriched_albums,
        "failed": failed,
        "skipped": skipped,
        "remaining": len(rows),
    }


# ---------------------------------------------------------------------------
# Auto-pipeline
# ---------------------------------------------------------------------------

_pipeline_running = False


def run_auto_pipeline() -> dict:
    """
    Full automated library pipeline: sync → enrich IDs → tags + bonus Spotify → BPM.

    Phase 1  — sync_library() (uncapped — always full library scan)
    Phase 1b — loop enrich_library(50) until remaining=0 or lib_enrich_ids_batch processed
    Phase 2a — enrich_lastfm_tags() always-on              } parallel via
    Phase 2b — enrich_spotify()     bonus only              } ThreadPoolExecutor
    Phase 3  — enrich_deezer_bpm()  capped at lib_enrich_bpm_batch tracks per run

    Artist enrichment chain: iTunes (Phase 1) → Deezer fallback (Phase 1) → Last.fm (Phase 2a).
    Spotify IDs are a bonus — nothing in the core pipeline depends on them.

    All stages are resumable via enrichment_meta. Interrupted runs pick up exactly where
    they left off on the next scheduled run. Returns a summary dict.
    """
    global _pipeline_running
    if _pipeline_running:
        logger.info("run_auto_pipeline: already running — skipping")
        return {"status": "skipped", "reason": "already_running"}

    _pipeline_running = True
    logger.info("run_auto_pipeline: starting")
    result: dict = {"status": "ok"}

    try:
        settings = rythmx_store.get_all_settings()

        def _bool(key: str, default: bool = True) -> bool:
            v = settings.get(key)
            if v is None:
                return default
            return str(v).lower() not in ("false", "0", "no")

        def _int(key: str, default: int) -> int:
            try:
                return int(settings.get(key, default))
            except (TypeError, ValueError):
                return default

        # ---- Phase 1: Full library sync (uncapped) ----
        sync_result = sync_library()
        result["sync"] = sync_result
        logger.info(
            "run_auto_pipeline: sync complete — artists=%d albums=%d tracks=%d",
            sync_result.get("artist_count", 0),
            sync_result.get("album_count", 0),
            sync_result.get("track_count", 0),
        )

        # ---- Phase 1b: ID enrichment loop (trickle-capped per run) ----
        if _bool("lib_enrich_ids"):
            batch_size = 50
            per_run_cap = _int("lib_enrich_ids_batch", 500)
            processed_this_run = 0
            total_enriched = 0
            total_failed = 0
            remaining = -1
            while processed_this_run < per_run_cap:
                r = enrich_library(batch_size=min(batch_size, per_run_cap - processed_this_run))
                batch_processed = r.get("enriched", 0) + r.get("skipped", 0) + r.get("failed", 0)
                total_enriched += r.get("enriched", 0)
                total_failed += r.get("failed", 0)
                remaining = r.get("remaining", 0)
                if batch_processed == 0:
                    break  # nothing fetched — all done or all excluded by enrichment_meta
                processed_this_run += batch_processed
            result["enrich_ids"] = {
                "enriched": total_enriched,
                "failed": total_failed,
                "processed_this_run": processed_this_run,
                "remaining": remaining,
            }
            logger.info(
                "run_auto_pipeline: ID enrichment — enriched=%d failed=%d processed=%d remaining=%d",
                total_enriched, total_failed, processed_this_run, remaining,
            )

        # ---- Phase 2: Last.fm tags (always-on) + bonus Spotify (parallel) ----
        phase2_tasks: dict[str, concurrent.futures.Future] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="lib-enrich"
        ) as pool:
            if _bool("lib_enrich_lastfm"):
                lastfm_batch = _int("lib_enrich_lastfm_batch", 100)
                phase2_tasks["lastfm"] = pool.submit(enrich_lastfm_tags, lastfm_batch)
            if _bool("lib_enrich_spotify"):
                spotify_batch = _int("lib_enrich_spotify_batch", 50)
                phase2_tasks["spotify"] = pool.submit(enrich_spotify, spotify_batch)

        for key, future in phase2_tasks.items():
            try:
                result[f"enrich_{key}"] = future.result()
            except Exception as e:
                logger.warning("run_auto_pipeline: phase 2 %s raised: %s", key, e)
                result[f"enrich_{key}"] = {"status": "error", "error": str(e)}

        # ---- Phase 3: Deezer BPM (requires deezer_id from Phase 1) ----
        if _bool("lib_enrich_bpm"):
            bpm_batch = _int("lib_enrich_bpm_batch", 200)
            bpm_result = enrich_deezer_bpm(batch_size=bpm_batch)
            result["enrich_bpm"] = bpm_result
            logger.info(
                "run_auto_pipeline: BPM — tracks=%d albums=%d failed=%d",
                bpm_result.get("enriched_tracks", 0),
                bpm_result.get("enriched_albums", 0),
                bpm_result.get("failed", 0),
            )

        rythmx_store.set_setting("library_last_synced", datetime.utcnow().isoformat())
        logger.info("run_auto_pipeline: complete")

    except Exception as e:
        logger.exception("run_auto_pipeline: unhandled error: %s", e)
        result["status"] = "error"
        result["error"] = str(e)
    finally:
        _pipeline_running = False

    return result


def get_deezer_bpm_status() -> dict:
    """
    Returns {enriched_albums, total_albums_with_deezer, enriched_tracks,
             total_tracks, last_run}.
    total_albums_with_deezer is the pool that can be enriched.
    """
    try:
        with _connect() as conn:
            total_albums = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE deezer_id IS NOT NULL"
            ).fetchone()[0]
            enriched_albums = conn.execute(
                """
                SELECT COUNT(*) FROM enrichment_meta
                WHERE source = 'deezer_bpm' AND entity_type = 'album'
                  AND status = 'found'
                """
            ).fetchone()[0]
            enriched_tracks = conn.execute(
                "SELECT COUNT(*) FROM lib_tracks WHERE tempo IS NOT NULL AND tempo > 0"
            ).fetchone()[0]
            total_tracks = conn.execute("SELECT COUNT(*) FROM lib_tracks").fetchone()[0]
    except Exception:
        total_albums = enriched_albums = enriched_tracks = total_tracks = 0

    last_run = rythmx_store.get_setting("deezer_bpm_last_run")
    return {
        "enriched_albums": enriched_albums,
        "total_albums": total_albums,
        "enriched_tracks": enriched_tracks,
        "total_tracks": total_tracks,
        "last_run": last_run,
    }
