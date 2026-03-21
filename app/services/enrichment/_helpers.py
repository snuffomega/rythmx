"""
_helpers.py — Shared validation, matching, and normalization utilities.

Used by multiple enrichment worker modules.  Moved here from the original
library_service.py monolith to avoid cross-module import cycles.
"""
import difflib
import logging
import re

from app import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------

_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\(\[](single|ep|deluxe|deluxe\s+edition|explicit|remaster(ed)?|'
    r'expanded|anniversary\s+edition|bonus\s+track[s]?|special\s+edition|'
    r'reissue)[\s\w]*[\)\]]',
    re.IGNORECASE,
)
# iTunes uses " - Single", " - EP", etc. as a trailing suffix (no brackets)
_ITUNES_TRAILING_RE = re.compile(
    r'\s+-\s+(Single|EP|Remixes)$',
    re.IGNORECASE,
)


def strip_title_suffixes(title: str) -> str:
    """Strip Plex/iTunes suffixes like [Single], (Deluxe Edition), ' - Single' before matching."""
    title = _TITLE_SUFFIX_RE.sub("", title).strip()
    title = _ITUNES_TRAILING_RE.sub("", title).strip()
    return title


def match_album_title(lib_title: str, api_title: str) -> float:
    """
    Score how well a lib_album title matches an API catalog title.
    Normalizes both sides via norm() + strip_title_suffixes() before comparing.
    Returns 1.0 for exact normalized match; SequenceMatcher ratio otherwise.
    Threshold for a 'match' in callers is >= 0.82.
    """
    from app.clients.music_client import norm
    a = norm(strip_title_suffixes(lib_title))
    b = norm(strip_title_suffixes(api_title))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def name_similarity_bonus(norm_target: str, norm_candidate: str) -> int:
    """Return a name similarity score bonus for use in validate_artist scoring."""
    if norm_target == norm_candidate:
        return 1000
    if norm_target in norm_candidate or norm_candidate in norm_target:
        return 500
    half = len(norm_target) // 2
    if half > 2 and norm_target[:half] in norm_candidate:
        return 300
    return 0


def normalize_lastfm_tags(raw_tags: list) -> list[str]:
    """
    Normalize raw Last.fm tags to canonical genre labels.
    raw_tags format: [[tag_name, tag_count], ...] or [tag_name, ...]
    Returns up to 5 matched canonical labels (deduplicated, in score order).
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


# ---------------------------------------------------------------------------
# Artist catalog persistence
# ---------------------------------------------------------------------------

def persist_artist_catalog(conn, artist_id: str, source: str, catalog: list[dict]) -> None:
    """
    Store an API-fetched album catalog for later gap analysis.
    Idempotent: INSERT OR REPLACE so re-enrichment refreshes stale rows.
    catalog items: {id, title, track_count?, record_type?}.
    """
    if not catalog:
        return
    for item in catalog:
        album_id = item.get("id", "")
        title = item.get("title", "")
        if not album_id or not title:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO lib_artist_catalog
                (artist_id, source, album_id, album_title, record_type, track_count, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (artist_id, source, album_id, title,
             item.get("record_type"), item.get("track_count")),
        )


# ---------------------------------------------------------------------------
# Universal artist validation (Phase 14 — Artist ID Registry)
# ---------------------------------------------------------------------------

def validate_artist(artist_name: str, lib_album_titles: list[str], source: str) -> dict | None:
    """
    Validate artist identity by scoring album catalog overlap against the user's library.

    Same logic applied across all sources:
      1. Search the API for artist name -> up to 5 candidates
      2. For each candidate (top 3): fetch album catalog + count title overlaps
      3. score = name_similarity_bonus + (overlap_count * 300)
      4. Confidence: 0 overlaps = 70 (name-only), 1-2 = 85 (medium), 3+ = 95 (high)
      5. If no name similarity at all -> reject (returns None)

    Returns {artist_id, confidence, album_catalog} or None.
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
        logger.debug("validate_artist: unsupported source '%s'", source)
        return None

    if not candidates:
        logger.debug("validate_artist: no candidates for '%s' on %s", artist_name, source)
        return None

    # --- Score each candidate ---
    best: dict | None = None
    best_score = -1

    for candidate in candidates[:3]:
        nb = name_similarity_bonus(norm_name, norm(candidate["name"]))
        if nb == 0:
            continue

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

        overlap = 0
        for lib_title in lib_album_titles:
            for api_title in catalog_titles:
                if match_album_title(lib_title, api_title) >= 0.82:
                    overlap += 1
                    break

        score = nb + (overlap * 300)
        if score > best_score:
            best_score = score
            best = {
                "candidate": candidate,
                "catalog": catalog,
                "overlap": overlap,
                "name_bonus": nb,
            }

    if best is None:
        logger.debug("validate_artist: no name-similar candidates for '%s' on %s",
                     artist_name, source)
        return None

    overlap = best["overlap"]
    if overlap >= 3:
        confidence = 95
    elif overlap >= 1:
        confidence = 85
    else:
        confidence = 70

    artist_id = best["candidate"].get("id") or best["candidate"].get("mbid", "")
    if not artist_id:
        logger.debug("validate_artist: '%s' on %s — no ID in candidate, skipping",
                     artist_name, source)
        return None

    logger.debug(
        "validate_artist: '%s' on %s -> id=%s overlap=%d confidence=%d",
        artist_name, source, artist_id, overlap, confidence,
    )
    return {
        "artist_id": artist_id,
        "confidence": confidence,
        "album_catalog": best["catalog"],
    }
