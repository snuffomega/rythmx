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

from app import config
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
     track_count, thumb_url_deezer, release_date_deezer, explicit, genre_deezer,
     catalog_source, confidence, user_dismissed,
     first_seen_at, last_checked_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deezer', ?, ?,
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
"""

_ITUNES_INSERT_SQL = """
INSERT OR IGNORE INTO lib_releases
    (id, artist_id, artist_name, artist_name_lower, title, title_lower,
     normalized_title, version_type, kind_itunes,
     itunes_album_id,
     track_count, thumb_url_itunes, release_date_itunes, explicit, label, genre_itunes,
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
# Secondary enrichment — UPDATE existing primary rows from non-primary catalog
# ---------------------------------------------------------------------------

_SECONDARY_MATCH_SQL = """
SELECT id FROM lib_releases
WHERE artist_id = ? AND normalized_title = ? AND catalog_source = ?
LIMIT 1
"""

_SECONDARY_ITUNES_UPDATE_SQL = """
UPDATE lib_releases
SET itunes_album_id  = COALESCE(itunes_album_id, ?),
    label            = COALESCE(label, ?),
    genre_itunes     = COALESCE(genre_itunes, ?),
    kind_itunes      = COALESCE(kind_itunes, ?),
    thumb_url_itunes = COALESCE(thumb_url_itunes, ?),
    release_date_itunes = COALESCE(release_date_itunes, ?),
    last_checked_at  = CURRENT_TIMESTAMP
WHERE id = ?
"""

_SECONDARY_DEEZER_UPDATE_SQL = """
UPDATE lib_releases
SET deezer_album_id  = COALESCE(deezer_album_id, ?),
    kind_deezer      = COALESCE(kind_deezer, ?),
    thumb_url_deezer = COALESCE(thumb_url_deezer, ?),
    release_date_deezer = COALESCE(release_date_deezer, ?),
    genre_deezer     = COALESCE(genre_deezer, ?),
    last_checked_at  = CURRENT_TIMESTAMP
WHERE id = ?
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
        # Non-primary catalog: enrich existing lib_releases rows (UPDATE only).
        # Primary source inserts rows; secondary fills in missing per-source fields.
        if source != config.CATALOG_PRIMARY:
            secondary_enriched = 0
            for item in catalog:
                album_id = item.get("id")
                title = item.get("title")
                if not album_id or not title:
                    continue
                album_id_str = str(album_id)
                cleaned_title, _ = detect_version_type(title)
                normalized_title = norm(cleaned_title)

                # Find matching primary row by artist + normalized title
                match = conn.execute(
                    _SECONDARY_MATCH_SQL,
                    (artist_id, normalized_title, config.CATALOG_PRIMARY),
                ).fetchone()
                if not match:
                    continue

                release_id = match[0]
                record_type = item.get("record_type")
                track_count = item.get("track_count") or None
                kind_value = _classify_kind(record_type, track_count)

                artwork_url = item.get("artwork_url") or None
                release_date = item.get("release_date") or None
                if release_date and "T" in release_date:
                    release_date = release_date.split("T")[0]

                try:
                    if source == "itunes":
                        label = item.get("label") or None
                        genre = item.get("genre") or None
                        conn.execute(
                            _SECONDARY_ITUNES_UPDATE_SQL,
                            (album_id_str, label, genre, kind_value,
                             artwork_url, release_date, release_id),
                        )
                    elif source == "deezer":
                        genre_deezer = item.get("genre") or None
                        conn.execute(
                            _SECONDARY_DEEZER_UPDATE_SQL,
                            (album_id_str, kind_value,
                             artwork_url, release_date, genre_deezer, release_id),
                        )
                    secondary_enriched += 1
                except Exception as exc:
                    logger.debug(
                        "catalog_promotion: secondary enrich failed for %s id=%s: %s",
                        source, album_id_str, exc,
                    )
            if secondary_enriched:
                logger.info(
                    "catalog_promotion: secondary enrichment from %s for '%s': %d rows updated",
                    source, artist_name, secondary_enriched,
                )
            continue
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
                            genre or None,
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

    return {"promoted": promoted, "merged": merged, "dismissed": dismissed}
