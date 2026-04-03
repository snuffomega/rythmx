"""
rich_musicbrainz_album.py — Stage 3 MusicBrainz album date worker.

Two enrichment paths:

  Path A — Release-ID direct (highest confidence):
    lib_albums.musicbrainz_release_id is populated (audio files tagged with
    MUSICBRAINZ_ALBUMID). Calls /ws/2/release/{id}?inc=release-groups to get
    the canonical Release Group and first-release-date. Near-zero coverage for
    most users unless files are tagged.

  Path B — Artist-MBID search (broad coverage):
    lib_albums whose artist has a known musicbrainz_id but the album itself
    has no musicbrainz_release_id. Calls browse_artist_release_groups() to
    fetch the artist's full release group catalog, then fuzzy-matches the
    album title (≥ 0.82 threshold, same as Stage 2b overlap validation).
    Lower confidence than Path A — writes with COALESCE guards so Path A
    results are never overwritten.

Writes:
  lib_albums.musicbrainz_release_group_id  — the Release Group MBID
  lib_albums.original_release_date_musicbrainz — first-release-date from the
                                                  Release Group (YYYY, YYYY-MM,
                                                  or YYYY-MM-DD)
"""
import logging

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta
from app.services.enrichment._helpers import strip_title_suffixes, match_album_title

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path A — Release-ID direct
# ---------------------------------------------------------------------------

_CANDIDATE_SQL = """
    SELECT id, title, musicbrainz_release_id FROM lib_albums
    WHERE musicbrainz_release_id IS NOT NULL
      AND original_release_date_musicbrainz IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL = """
    SELECT COUNT(*) FROM lib_albums
    WHERE musicbrainz_release_id IS NOT NULL
      AND original_release_date_musicbrainz IS NULL
      AND removed_at IS NULL
      AND id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item(conn, row):
    from app.clients.musicbrainz_client import get_release

    album_id = row["id"]
    album_title = row["title"]
    release_mbid = row["musicbrainz_release_id"]

    info = get_release(release_mbid)
    if info:
        rg_id = info.get("release_group_id") or None
        first_date = info.get("first_release_date") or None

        if rg_id or first_date:
            conn.execute(
                """
                UPDATE lib_albums
                SET musicbrainz_release_group_id       = COALESCE(musicbrainz_release_group_id, ?),
                    original_release_date_musicbrainz  = COALESCE(original_release_date_musicbrainz, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (rg_id, first_date, album_id),
            )
            write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "found")
            logger.debug(
                "enrich_musicbrainz_album_rich (path-A): '%s' -> rg_id=%s first_date=%s",
                album_title, rg_id, first_date,
            )
            return "found"

    write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "not_found")
    return "not_found"


# ---------------------------------------------------------------------------
# Path B — Artist-MBID search fallback
# ---------------------------------------------------------------------------

# Albums where the artist has a known MB MBID but the album has no release ID
# and hasn't been enriched yet (or had a not_found that's past retry window).
_CANDIDATE_SQL_B = """
    SELECT la.id, la.title, la.local_title, ar.musicbrainz_id AS artist_mbid
    FROM lib_albums la
    JOIN lib_artists ar ON ar.id = la.artist_id
    WHERE la.musicbrainz_release_group_id IS NULL
      AND la.musicbrainz_release_id IS NULL
      AND ar.musicbrainz_id IS NOT NULL
      AND la.removed_at IS NULL
      AND la.id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""

_REMAINING_SQL_B = """
    SELECT COUNT(*)
    FROM lib_albums la
    JOIN lib_artists ar ON ar.id = la.artist_id
    WHERE la.musicbrainz_release_group_id IS NULL
      AND la.musicbrainz_release_id IS NULL
      AND ar.musicbrainz_id IS NOT NULL
      AND la.removed_at IS NULL
      AND la.id NOT IN (
          SELECT entity_id FROM enrichment_meta
          WHERE entity_type = 'album' AND source = 'musicbrainz_album_rich'
            AND (status = 'found'
                 OR (status = 'not_found'
                     AND (retry_after IS NULL OR retry_after > date('now'))))
      )
"""


def _process_item_via_artist(conn, row):
    from app.clients.musicbrainz_client import browse_artist_release_groups

    album_id = row["id"]
    # Prefer local_title (suffix-stripped at ingest) then title
    raw_title = row["local_title"] or row["title"]
    album_title = strip_title_suffixes(raw_title)
    artist_mbid = row["artist_mbid"]

    release_groups = browse_artist_release_groups(artist_mbid)
    if not release_groups:
        write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "not_found")
        return "not_found"

    # Fuzzy-match album title against each release group title (≥ 0.82 = same
    # threshold used in Stage 2b MusicBrainz ID overlap validation).
    best_rg = None
    best_score = 0.0
    for rg in release_groups:
        score = match_album_title(album_title, strip_title_suffixes(rg["title"]))
        if score > best_score:
            best_score = score
            best_rg = rg

    if best_rg and best_score >= 0.82:
        rg_id = best_rg["id"]
        first_date = best_rg.get("first_release_date") or None
        conn.execute(
            """
            UPDATE lib_albums
            SET musicbrainz_release_group_id       = COALESCE(musicbrainz_release_group_id, ?),
                original_release_date_musicbrainz  = COALESCE(original_release_date_musicbrainz, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rg_id, first_date, album_id),
        )
        write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "found")
        logger.debug(
            "enrich_musicbrainz_album_rich (path-B): '%s' score=%.2f -> rg_id=%s first_date=%s",
            raw_title, best_score, rg_id, first_date,
        )
        return "found"

    write_enrichment_meta(conn, "musicbrainz_album_rich", "album", album_id, "not_found")
    return "not_found"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def enrich_musicbrainz_album_rich(batch_size=50, stop_event=None, on_progress=None):
    """Stage 3 — MusicBrainz album rich data: release_group_id, original_release_date.

    Runs Path A (release-ID direct) then Path B (artist-MBID search fallback)
    in sequence. Each path is gated by its own candidate SQL so work is never
    duplicated and COALESCE guards prevent double-writes.
    """
    result_a = run_enrichment_loop(
        worker_name="enrich_musicbrainz_album_rich/path-A",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="musicbrainz_album_rich",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )

    if stop_event and stop_event.is_set():
        return result_a

    result_b = run_enrichment_loop(
        worker_name="enrich_musicbrainz_album_rich/path-B",
        candidate_sql=_CANDIDATE_SQL_B,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL_B,
        remaining_params=(),
        source="musicbrainz_album_rich",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item_via_artist,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )

    # Merge result dicts: sum numeric fields from both paths
    merged = {}
    for key in ("enriched", "skipped", "failed", "remaining"):
        merged[key] = result_a.get(key, 0) + result_b.get(key, 0)
    return merged
