"""
identity_resolver.py — confidence-based artist identity resolution.

Algorithm:
  1. Check cc.db cache — return immediately if confidence >= 85 and not stale.
  2. Fetch Last.fm top tracks for the artist (artist.getTopTracks, public endpoint).
  3. Search iTunes for name-matched artist candidates.
  4. Score candidates: exact norm match +1000, contains +500, + popularity if available.
  5. For the top 3 candidates, fetch their iTunes top tracks.
  6. Count normalized title overlap with Last.fm top tracks.
  7. Boost score by overlap * 200; pick winner.
  8. Confidence: base=80, 1 overlap→86, 2→92, 3+→100.
  9. Cache result in cc.db (additive COALESCE upsert — never overwrites good existing IDs).

Cross-reference: Last.fm ↔ iTunes only. SoulSync is not involved in identity.
"""
import re
import time
import logging

logger = logging.getLogger(__name__)

# Cache: re-resolve after 30 days OR any time confidence < 85
_CACHE_TTL_SECONDS = 30 * 24 * 3600

# Titles that are too generic to be useful overlap signals
_COMMON_TITLES = frozenset({
    "intro", "outro", "interlude", "untitled", "track", "bonus track",
    "skit", "instrumental", "remix", "live", "acoustic", "home",
})

# How many top candidates to fetch iTunes top tracks for (API calls)
_TOP_N_OVERLAP = 3


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _norm_title(s: str) -> str:
    """
    Normalize a track title for overlap comparison.
    Strips feat./ft./featuring (parenthetical or trailing), then applies
    music_client.norm() (NFKC + lowercase + strip articles + no punctuation).
    """
    from app.music_client import norm
    if not s:
        return ""
    s = re.sub(
        r'\s*[\(\[][^)\]]*(?:feat|ft|featuring)[.\s][^\)\]]*[\)\]]',
        "", s, flags=re.IGNORECASE
    )
    s = re.sub(r'\s*(?:feat|ft|featuring)[.\s].*$', "", s, flags=re.IGNORECASE)
    return norm(s)


def _is_ultra_common(title_norm: str) -> bool:
    """Return True for titles too generic to be useful overlap signals."""
    if not title_norm or len(title_norm) <= 2:
        return True
    if title_norm in _COMMON_TITLES:
        return True
    if re.fullmatch(r'(track)\s*\d{1,3}', title_norm):
        return True
    return False


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------

def resolve_artist(lastfm_name: str, force: bool = False) -> dict:
    """
    Resolve a Last.fm artist name to a confirmed iTunes artist ID with confidence score.

    Returns:
        {
            "lastfm_name": str,
            "itunes_artist_id": str | None,
            "itunes_artist_name": str | None,
            "confidence": int,           # 80 / 86 / 92 / 100
            "confidence_level": str,     # "low" / "medium" / "high"
            "reason_codes": list[str],
            "debug_candidates": list,    # [{name, id, score, overlap}] for top candidates
        }

    force=True bypasses the cache and forces a fresh resolution.
    """
    from app.db import cc_store
    from app import last_fm_client
    from app.music_client import norm, search_artist_candidates_itunes, get_artist_top_tracks_itunes

    result = {
        "lastfm_name": lastfm_name,
        "itunes_artist_id": None,
        "itunes_artist_name": None,
        "confidence": 80,
        "confidence_level": "medium",
        "reason_codes": [],
        "debug_candidates": [],
    }

    if not lastfm_name or not lastfm_name.strip():
        result["reason_codes"].append("empty_name")
        return result

    lastfm_name = lastfm_name.strip()
    norm_name = norm(lastfm_name)

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    if not force:
        cached = cc_store.get_cached_artist(lastfm_name)
        if cached and cached.get("itunes_artist_id"):
            age = int(time.time()) - int(cached.get("last_resolved_ts") or 0)
            cached_conf = int(cached.get("confidence") or 0)
            if cached_conf >= 85 and age < _CACHE_TTL_SECONDS:
                result.update({
                    "itunes_artist_id": cached["itunes_artist_id"],
                    "itunes_artist_name": None,  # not stored in cache
                    "confidence": cached_conf,
                    "confidence_level": "high",
                    "reason_codes": ["cache_hit"],
                })
                logger.debug("Identity cache hit: %s (confidence=%d)", lastfm_name, cached_conf)
                return result
            if cached_conf < 85:
                result["reason_codes"].append("cache_low_confidence_retry")
            else:
                result["reason_codes"].append("cache_stale_retry")

    # ------------------------------------------------------------------
    # Step 1 — Last.fm top tracks for this artist
    # ------------------------------------------------------------------
    raw_lastfm_titles = last_fm_client.get_artist_top_tracks(lastfm_name, limit=10)
    lastfm_titles_norm = []
    for t in raw_lastfm_titles:
        tn = _norm_title(t)
        if not _is_ultra_common(tn):
            lastfm_titles_norm.append(tn)

    if lastfm_titles_norm:
        result["reason_codes"].append("lastfm_top_tracks_available")
    else:
        result["reason_codes"].append("lastfm_top_tracks_unavailable")

    # ------------------------------------------------------------------
    # Step 2 — iTunes artist candidate search
    # ------------------------------------------------------------------
    raw_candidates = search_artist_candidates_itunes(lastfm_name, limit=5)
    if not raw_candidates:
        result["reason_codes"].append("itunes_no_candidates")
        logger.info("Identity: no iTunes candidates for '%s'", lastfm_name)
        _write_cache(lastfm_name, result)
        return result

    result["reason_codes"].append("itunes_search_ok")

    # ------------------------------------------------------------------
    # Step 3 — Score candidates by name similarity
    # ------------------------------------------------------------------
    candidates = []
    for c in raw_candidates:
        cname_norm = norm(c["name"])
        score = 0
        if cname_norm == norm_name:
            score += 1000
        elif norm_name and norm_name in cname_norm:
            score += 500
        elif norm_name and cname_norm in norm_name and len(cname_norm) > 2:
            score += 300
        candidates.append({
            "name": c["name"],
            "id": c["id"],
            "score": score,
            "overlap": None,
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # ------------------------------------------------------------------
    # Step 4 — Top-track overlap for top N candidates (iTunes API calls)
    # ------------------------------------------------------------------
    if lastfm_titles_norm:
        for c in candidates[:_TOP_N_OVERLAP]:
            itunes_raw = get_artist_top_tracks_itunes(c["id"], limit=10)
            itunes_norm = []
            for t in itunes_raw:
                tn = _norm_title(t)
                if not _is_ultra_common(tn):
                    itunes_norm.append(tn)

            overlap = len(set(lastfm_titles_norm) & set(itunes_norm))
            c["overlap"] = overlap
            c["score"] += overlap * 200

        # Re-sort after overlap boost
        candidates.sort(key=lambda x: x["score"], reverse=True)

    result["debug_candidates"] = candidates[:5]

    # ------------------------------------------------------------------
    # Step 5 — Pick winner + compute confidence
    # ------------------------------------------------------------------
    winner = candidates[0]
    result["itunes_artist_id"] = winner["id"]
    result["itunes_artist_name"] = winner["name"]
    result["reason_codes"].append("itunes_match_via_search")

    overlap_val = winner.get("overlap")
    if isinstance(overlap_val, int):
        result["reason_codes"].append(f"track_overlap:{overlap_val}")
        if overlap_val >= 3:
            confidence = 100
            resolution_method = "track_overlap_3plus"
        elif overlap_val == 2:
            confidence = 92
            resolution_method = "track_overlap_2"
        elif overlap_val == 1:
            confidence = 86
            resolution_method = "track_overlap_1"
        else:
            confidence = 80
            resolution_method = "name_only"
    else:
        # No Last.fm top tracks available — name match only
        confidence = 80
        resolution_method = "name_only"

    result["confidence"] = confidence
    result["confidence_level"] = (
        "high" if confidence >= 85 else ("medium" if confidence >= 70 else "low")
    )
    result["reason_codes"].append(resolution_method)

    logger.info(
        "Identity resolved: '%s' → iTunes:%s '%s' (confidence=%d, overlap=%s)",
        lastfm_name, winner["id"], winner["name"], confidence, overlap_val,
    )

    # ------------------------------------------------------------------
    # Step 6 — Write to cache
    # ------------------------------------------------------------------
    _write_cache(lastfm_name, result, resolution_method=resolution_method)

    return result


def _write_cache(lastfm_name: str, result: dict, resolution_method: str = None):
    """Persist resolution result to cc.db artist_identity_cache (additive COALESCE upsert)."""
    try:
        from app.db import cc_store
        cc_store.cache_artist(
            lastfm_name,
            itunes_artist_id=result.get("itunes_artist_id"),
            confidence=result.get("confidence", 80),
            resolution_method=resolution_method,
        )
    except Exception as e:
        logger.warning("Identity cache write failed for '%s': %s", lastfm_name, e)
