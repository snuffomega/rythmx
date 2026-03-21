"""
catalog_promotion.py — Promote raw API catalog data into lib_releases rows.

Handles normalization, version-type detection, kind classification, and
automatic compilation dismissal.  Called inline during enrich_library()
per artist — catalogs are already in memory, no extra API calls needed.
"""
import logging
import re

from app.clients.music_client import norm
from app.services.enrichment._helpers import detect_version_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compilation title patterns — auto-dismissed on INSERT
# ---------------------------------------------------------------------------

_COMPILATION_PATTERNS = re.compile(
    r"greatest\s+hits|best\s+of\b|complete\s+collection|"
    r"\banthology\b|\bretrospective\b|(?:^the\s+)?essential\s+\w|definitive\s+collection|"
    r"the\s+very\s+best|ultimate\s+collection",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------


def _classify_kind(record_type: str | None, track_count: int | None) -> str:
    """Derive kind from record_type or track_count heuristic.

    Priority:
      1. record_type (from API) — mapped to canonical kind
      2. track_count heuristic — 1-3 single, 4-6 ep, 7+ album
      3. Fallback — "album"
    """
    if record_type:
        rt = record_type.strip().lower()
        if rt == "compile":
            return "compilation"
        if rt in ("album", "single", "ep"):
            return rt
        return "album"

    if track_count is not None:
        if track_count <= 3:
            return "single"
        if track_count <= 6:
            return "ep"
        return "album"

    return "album"


# ---------------------------------------------------------------------------
# SQL templates (separate for deezer / itunes — no f-string substitution)
# ---------------------------------------------------------------------------

_DEEZER_UPSERT_SQL = """
INSERT INTO lib_releases
    (id, artist_id, artist_name, artist_name_lower, title, title_lower,
     normalized_title, version_type, kind, deezer_album_id,
     track_count, thumb_url, catalog_source, confidence, user_dismissed,
     first_seen_at, last_checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deezer', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT(artist_name_lower, title_lower, kind) DO UPDATE SET
    deezer_album_id = COALESCE(excluded.deezer_album_id, lib_releases.deezer_album_id),
    track_count = COALESCE(excluded.track_count, lib_releases.track_count),
    thumb_url = COALESCE(NULLIF(excluded.thumb_url, ''), lib_releases.thumb_url),
    catalog_source = CASE
        WHEN lib_releases.catalog_source IS NULL THEN excluded.catalog_source
        WHEN lib_releases.catalog_source != excluded.catalog_source THEN 'both'
        ELSE lib_releases.catalog_source
    END,
    confidence = MAX(lib_releases.confidence, excluded.confidence),
    normalized_title = COALESCE(lib_releases.normalized_title, excluded.normalized_title),
    version_type = COALESCE(lib_releases.version_type, excluded.version_type),
    last_checked_at = CURRENT_TIMESTAMP
"""

_ITUNES_UPSERT_SQL = """
INSERT INTO lib_releases
    (id, artist_id, artist_name, artist_name_lower, title, title_lower,
     normalized_title, version_type, kind, itunes_album_id,
     track_count, thumb_url, catalog_source, confidence, user_dismissed,
     first_seen_at, last_checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'itunes', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT(artist_name_lower, title_lower, kind) DO UPDATE SET
    itunes_album_id = COALESCE(excluded.itunes_album_id, lib_releases.itunes_album_id),
    track_count = COALESCE(excluded.track_count, lib_releases.track_count),
    thumb_url = COALESCE(NULLIF(excluded.thumb_url, ''), lib_releases.thumb_url),
    catalog_source = CASE
        WHEN lib_releases.catalog_source IS NULL THEN excluded.catalog_source
        WHEN lib_releases.catalog_source != excluded.catalog_source THEN 'both'
        ELSE lib_releases.catalog_source
    END,
    confidence = MAX(lib_releases.confidence, excluded.confidence),
    normalized_title = COALESCE(lib_releases.normalized_title, excluded.normalized_title),
    version_type = COALESCE(lib_releases.version_type, excluded.version_type),
    last_checked_at = CURRENT_TIMESTAMP
"""

_EXISTS_SQL = """
SELECT 1 FROM lib_releases
WHERE artist_name_lower = ? AND title_lower = ? AND kind = ?
LIMIT 1
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def promote_catalog_to_releases(
    conn,
    artist_id: str,
    artist_name: str,
    itunes_catalog: list[dict],
    deezer_catalog: list[dict],
    validation_confidence: int = 0,
) -> dict:
    """Promote raw API catalog entries into lib_releases rows.

    Processes Deezer first (explicit record_type), then iTunes.
    Returns {promoted: int, merged: int, dismissed: int}.
    """
    promoted = 0
    merged = 0
    dismissed = 0

    artist_name_lower = artist_name.lower()

    for source, catalog, sql in (
        ("deezer", deezer_catalog, _DEEZER_UPSERT_SQL),
        ("itunes", itunes_catalog, _ITUNES_UPSERT_SQL),
    ):
        # Deduplicate by album_id — API can return same ID with variant titles
        # (e.g., clean + explicit). Keep first occurrence.
        seen_ids: set[str] = set()

        for item in catalog:
            album_id = item.get("id")
            title = item.get("title")

            # Skip if no id or no title
            if not album_id or not title:
                continue

            # Validate ID format: numeric only
            if not str(album_id).isdigit():
                logger.warning(
                    "catalog_promotion: skipping non-numeric %s album_id=%r for '%s'",
                    source, album_id, artist_name,
                )
                continue

            album_id_str = str(album_id)

            # Skip duplicate album_ids within same source
            if album_id_str in seen_ids:
                continue
            seen_ids.add(album_id_str)

            # Detect version type
            cleaned_title, version_type = detect_version_type(title)

            # Normalize
            normalized_title = norm(cleaned_title)

            # Classify kind
            record_type = item.get("record_type")
            track_count = item.get("track_count")
            kind = _classify_kind(record_type, track_count)

            # Build release_id
            release_id = kind + "_" + source + "_" + album_id_str

            # Artwork URL (already in API response — zero extra calls)
            artwork_url = item.get("artwork_url") or ""

            # Auto-dismiss compilations
            user_dismissed = 1 if _COMPILATION_PATTERNS.search(title) else 0
            if user_dismissed:
                dismissed += 1

            title_lower = title.lower()

            # Check existence before upsert to track promoted vs merged
            exists = conn.execute(
                _EXISTS_SQL, (artist_name_lower, title_lower, kind)
            ).fetchone()

            # Execute upsert
            try:
                conn.execute(
                    sql,
                    (
                        release_id,
                        artist_id,
                        artist_name,
                        artist_name_lower,
                        title,
                        title_lower,
                        normalized_title,
                        version_type,
                        kind,
                        album_id_str,
                        track_count,
                        artwork_url or None,
                        validation_confidence,
                        user_dismissed,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "catalog_promotion: upsert failed for %s id=%s title=%r: %s",
                    source, album_id_str, title, exc,
                )
                continue

            if exists:
                merged += 1
            else:
                promoted += 1

    logger.info(
        "catalog_promotion: artist='%s' promoted=%d merged=%d dismissed=%d",
        artist_name, promoted, merged, dismissed,
    )

    return {"promoted": promoted, "merged": merged, "dismissed": dismissed}
