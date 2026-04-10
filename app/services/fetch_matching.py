"""
Shared fetch matching helpers used by downloader plugins and fetch probe tooling.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from app.services.enrichment._helpers import detect_version_type, match_album_title, strip_title_suffixes


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"\((?:feat|ft|featuring)[^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:feat|ft|featuring)\.?\s+.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(value: str) -> set[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    return {token for token in normalized.split(" ") if token}


def _token_overlap(a: str, b: str) -> float:
    a_tokens = _tokenize(a)
    b_tokens = _tokenize(b)
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        raw = str(value).strip()
        if not raw:
            return None
        return int(float(raw))
    except Exception:
        return None


def _expected_year(metadata: dict[str, Any]) -> int | None:
    raw = str((metadata or {}).get("release_date") or "").strip()
    if not raw:
        return None
    try:
        return int(raw[:4])
    except Exception:
        return None


def _expected_tidal_ids(metadata: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
        raw = str((metadata or {}).get(key) or "").strip()
        if raw.isdigit():
            out.add(raw)
    return out


def _expected_track_titles(metadata: dict[str, Any]) -> set[str]:
    raw = (metadata or {}).get("track_titles")
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        normalized = _normalize_text(str(item or ""))
        if normalized:
            out.add(normalized)
    return out


def score_tidarr_candidate(
    *,
    artist: str,
    album: str,
    metadata: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    expected_artist = _normalize_text(artist)
    expected_album = _normalize_text(album)
    candidate_artist = _normalize_text(str(candidate.get("artist") or ""))
    candidate_album = _normalize_text(str(candidate.get("album") or ""))

    artist_ratio = difflib.SequenceMatcher(None, expected_artist, candidate_artist).ratio()
    artist_overlap = _token_overlap(expected_artist, candidate_artist)
    artist_score = (artist_ratio * 0.65) + (artist_overlap * 0.35)
    artist_exact = expected_artist == candidate_artist and bool(expected_artist)

    album_title_score = float(
        match_album_title(
            str(album or ""),
            str(candidate.get("album") or ""),
        )
    )
    expected_album_stripped = _normalize_text(strip_title_suffixes(album))
    candidate_album_stripped = _normalize_text(strip_title_suffixes(str(candidate.get("album") or "")))
    album_exact = expected_album_stripped == candidate_album_stripped and bool(expected_album_stripped)

    expected_version = detect_version_type(str(album or ""))[1]
    candidate_version = detect_version_type(str(candidate.get("album") or ""))[1]
    if expected_version == candidate_version:
        version_score = 1.0
    elif expected_version == "original" and candidate_version in {"deluxe", "expanded", "anniversary", "remaster"}:
        version_score = 0.1
    else:
        version_score = 0.45

    year_score = 0.5
    expected_year = _expected_year(metadata)
    candidate_year = _to_int(candidate.get("year"))
    if expected_year is not None and candidate_year is not None:
        delta = abs(expected_year - candidate_year)
        if delta == 0:
            year_score = 1.0
        elif delta == 1:
            year_score = 0.8
        elif delta <= 3:
            year_score = 0.35
        else:
            year_score = 0.0

    track_count_score = 0.5
    expected_track_count = _to_int(metadata.get("track_count"))
    candidate_track_count = _to_int(candidate.get("track_count"))
    if expected_track_count is not None and candidate_track_count is not None:
        delta = abs(expected_track_count - candidate_track_count)
        if delta == 0:
            track_count_score = 1.0
        elif delta <= 1:
            track_count_score = 0.8
        elif delta <= 3:
            track_count_score = 0.45
        else:
            track_count_score = 0.0

    track_overlap_score = 0.5
    expected_tracks = _expected_track_titles(metadata)
    candidate_track_titles_raw = candidate.get("track_titles")
    if expected_tracks and isinstance(candidate_track_titles_raw, list):
        candidate_tracks = {_normalize_text(str(v or "")) for v in candidate_track_titles_raw}
        candidate_tracks = {v for v in candidate_tracks if v}
        if candidate_tracks:
            track_overlap_score = len(expected_tracks & candidate_tracks) / max(1, len(expected_tracks))

    id_score = 0.0
    expected_ids = _expected_tidal_ids(metadata)
    tidal_id = str(candidate.get("tidal_id") or "").strip()
    if tidal_id and tidal_id in expected_ids:
        id_score = 1.0

    score = (
        (id_score * 0.35)
        + (artist_score * 0.17)
        + (album_title_score * 0.25)
        + (version_score * 0.06)
        + (year_score * 0.07)
        + (track_count_score * 0.07)
        + (track_overlap_score * 0.03)
    )
    if id_score <= 0:
        # When we do not have a stable ID anchor, trust strong text agreement more.
        score += (artist_score * 0.10) + (album_title_score * 0.20)
    if artist_exact:
        score += 0.03
    if album_exact:
        score += 0.04
    if id_score <= 0 and artist_exact and album_exact:
        score = max(score, 0.96)
    score = max(0.0, min(1.0, score))

    strategy = "search_score"
    if id_score >= 1.0:
        strategy = "id_signature"
    if str(candidate.get("source") or "").strip() == "manual_id":
        strategy = "manual_id"

    reasons = [
        f"artist_score={artist_score:.2f}",
        f"album_score={album_title_score:.2f}",
        f"version={expected_version}->{candidate_version}",
        f"year_score={year_score:.2f}",
        f"track_count_score={track_count_score:.2f}",
        f"track_overlap_score={track_overlap_score:.2f}",
    ]
    if id_score > 0:
        reasons.append("id_match=1.00")

    return {
        "score": round(score, 6),
        "strategy": strategy,
        "reasons": reasons,
        "artist_score": round(artist_score, 6),
        "album_score": round(album_title_score, 6),
        "id_score": round(id_score, 6),
    }


def evaluate_tidarr_candidates(
    *,
    artist: str,
    album: str,
    metadata: dict[str, Any],
    candidates: list[dict[str, Any]],
    min_confidence: float = 0.86,
    ambiguous_margin: float = 0.04,
    snapshot_limit: int = 8,
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        cand = dict(candidate)
        metrics = score_tidarr_candidate(
            artist=artist,
            album=album,
            metadata=metadata,
            candidate=cand,
        )
        cand["score"] = metrics["score"]
        cand["match_strategy"] = metrics["strategy"]
        cand["match_reasons"] = metrics["reasons"]
        scored.append(cand)

    scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    top = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    best_score = float(top.get("score") or 0.0) if top else 0.0
    second_score = float(second.get("score") or 0.0) if second else 0.0
    margin = best_score - second_score

    if not top:
        status = "unresolved"
        strategy = "search_score"
        reasons = ["No candidates returned from provider search"]
    elif best_score >= min_confidence and margin >= ambiguous_margin:
        status = "confident"
        strategy = str(top.get("match_strategy") or "search_score")
        reasons = list(top.get("match_reasons") or [])
    elif best_score >= max(0.70, min_confidence - 0.06):
        status = "ambiguous"
        strategy = str(top.get("match_strategy") or "search_score")
        reasons = list(top.get("match_reasons") or [])
        reasons.append(f"ambiguous_margin={margin:.2f}")
    else:
        status = "unresolved"
        strategy = str(top.get("match_strategy") or "search_score")
        reasons = list(top.get("match_reasons") or [])
        reasons.append(f"below_threshold={best_score:.2f}<{min_confidence:.2f}")

    snapshot: list[dict[str, Any]] = []
    for cand in scored[: max(1, snapshot_limit)]:
        snapshot.append(
            {
                "tidal_id": str(cand.get("tidal_id") or ""),
                "artist": str(cand.get("artist") or ""),
                "album": str(cand.get("album") or ""),
                "quality": str(cand.get("quality") or ""),
                "year": cand.get("year"),
                "track_count": cand.get("track_count"),
                "source": str(cand.get("source") or "search"),
                "score": float(cand.get("score") or 0.0),
            }
        )

    return {
        "status": status,
        "match_status": status,
        "match_strategy": strategy,
        "match_confidence": round(best_score, 6),
        "match_reasons": reasons,
        "selected": top,
        "candidates": snapshot,
        "best_score": round(best_score, 6),
        "second_score": round(second_score, 6),
        "margin": round(margin, 6),
    }
