"""
catalog_promotion.py — Promote raw API catalog data into lib_releases rows.

Handles normalization, version-type detection, kind classification, and
automatic compilation dismissal.  Called inline during enrich_library()
per artist — catalogs are already in memory, no extra API calls needed.

Store-everything pattern: each source gets its own row keyed by
{source}_{album_id}.  INSERT OR IGNORE on PK — never overwrite.
A separate UPDATE touches last_checked_at for re-verified rows.
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

    if track_count is not None and track_count > 0:
        if track_count <= 3:
            return "single"
        if track_count <= 6:
            return "ep"
        return "album"

    return "album"


# ---------------------------------------------------------------------------
# SQL templates — INSERT OR IGNORE + separate UPDATE for last_checked_at
# ---------------------------------------------------------------------------

_DEEZER_INSERT_SQL = """
INSERT OR IGNORE INTO lib_releases
    (id, artist_id, artist_name, artist_name_lower, title, title_lower,
     normalized_title, version_type, kind_deezer,
     deezer_album_id,
     track_count, thumb_url, release_date, explicit,
     catalog_source, confidence, user_dismissed,
     first_seen_at, last_checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deezer', ?, ?,
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
"""

_ITUNES_INSERT_SQL = """
INSERT OR IGNORE INTO lib_releases
    (id, artist_id, artist_name, artist_name_lower, title, title_lower,
     normalized_title, version_type, kind_itunes,
     itunes_album_id,
     track_count, thumb_url, release_date, explicit, label, genre_itunes,
     catalog_source, confidence, user_dismissed,
     first_seen_at, last_checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'itunes', ?, ?,
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
"""

_UPDATE_LAST_CHECKED_SQL = """
UPDATE lib_releases SET last_checked_at = CURRENT_TIMESTAMP WHERE id = ?
"""

_EXISTS_SQL = """
SELECT 1 FROM lib_releases WHERE id = ? LIMIT 1
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
    Each source gets its own row keyed by {source}_{album_id}.
    INSERT OR IGNORE on PK — never overwrite existing rows.
    Returns {promoted: int, merged: int, dismissed: int}.
    """
    promoted = 0
    merged = 0
    dismissed = 0

    artist_name_lower = artist_name.lower()

    for source, catalog, sql in (
        ("deezer", deezer_catalog, _DEEZER_INSERT_SQL),
        ("itunes", itunes_catalog, _ITUNES_INSERT_SQL),
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

            # Detect version type from raw title
            cleaned_title, version_type = detect_version_type(title)

            # normalized_title = norm(cleaned_title) — for grouping only
            normalized_title = norm(cleaned_title)

            # Classify kind (per-source column, not the shared kind column)
            record_type = item.get("record_type")
            track_count = item.get("track_count") or None
            kind_value = _classify_kind(record_type, track_count)

            # PK = {source}_{album_id} — no kind prefix
            release_id = source + "_" + album_id_str

            # Artwork URL (already in API response — zero extra calls)
            artwork_url = item.get("artwork_url") or ""

            # Release date
            release_date = item.get("release_date") or None
            # iTunes returns ISO 8601 (2024-01-15T08:00:00Z) — trim to date
            if release_date and "T" in release_date:
                release_date = release_date.split("T")[0]

            # Auto-dismiss compilations — unless user manually un-dismissed
            user_dismissed = 0
            if _COMPILATION_PATTERNS.search(title):
                # Check if user manually overrode dismiss for this release
                manual_override = conn.execute(
                    "SELECT 1 FROM user_release_prefs "
                    "WHERE release_id = ? AND dismissed = 0 AND source = 'manual'",
                    (release_id,),
                ).fetchone()
                if not manual_override:
                    user_dismissed = 1
                    dismissed += 1

            # title_lower = raw title.lower() (NOT cleaned title)
            title_lower = title.lower()

            # Check existence before INSERT OR IGNORE to track promoted vs merged
            exists = conn.execute(
                _EXISTS_SQL, (release_id,)
            ).fetchone()

            # Extract source-specific fields
            is_explicit = 1 if item.get("explicit") else 0
            label = item.get("label") or ""
            genre = item.get("genre") or ""

            # Execute INSERT OR IGNORE — params differ per source
            try:
                if source == "deezer":
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
                            kind_value,
                            album_id_str,
                            track_count,
                            artwork_url or None,
                            release_date,
                            is_explicit,
                            validation_confidence,
                            user_dismissed,
                        ),
                    )
                else:
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
                            kind_value,
                            album_id_str,
                            track_count,
                            artwork_url or None,
                            release_date,
                            is_explicit,
                            label or None,
                            genre or None,
                            validation_confidence,
                            user_dismissed,
                        ),
                    )

            except Exception as exc:
                logger.warning(
                    "catalog_promotion: insert failed for %s id=%s title=%r: %s",
                    source, album_id_str, title, exc,
                )
                continue

            if exists:
                # Update last_checked_at for already-existing rows
                conn.execute(_UPDATE_LAST_CHECKED_SQL, (release_id,))
                merged += 1
            else:
                promoted += 1

    logger.info(
        "catalog_promotion: artist='%s' promoted=%d merged=%d dismissed=%d",
        artist_name, promoted, merged, dismissed,
    )

    # Refresh canonical_release_id groupings for this artist
    try:
        from app.db.rythmx_store import populate_canonical_release_ids
        populate_canonical_release_ids(artist_id=artist_id)
    except Exception as exc:
        logger.warning("catalog_promotion: canonical refresh failed for %s: %s", artist_name, exc)

    return {"promoted": promoted, "merged": merged, "dismissed": dismissed}
