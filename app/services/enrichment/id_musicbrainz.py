"""
id_musicbrainz.py — Stage 2b MusicBrainz Direct ID worker.

Populates lib_artists.musicbrainz_id.

Shortcut: if lastfm_mbid is already populated, copies it directly at
confidence 100 (same MBID, zero API calls). Only searches MusicBrainz
API when lastfm_mbid is NULL.
"""
import logging
import threading

from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.enrichment._helpers import strip_title_suffixes, match_album_title

logger = logging.getLogger(__name__)


def enrich_artist_ids_musicbrainz(
    batch_size: int = 50,
    stop_event: threading.Event | None = None,
    on_progress: "callable | None" = None,
) -> dict:
    """Stage 2b — MusicBrainz Direct ID: validate + store musicbrainz_id."""
    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, lastfm_mbid FROM lib_artists
                WHERE musicbrainz_id IS NULL
                  AND removed_at IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'musicbrainz_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_artist_ids_musicbrainz: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]
        lastfm_mbid = artist["lastfm_mbid"]

        try:
            conn = _connect()
        except Exception:
            failed += 1
            continue

        try:
            if lastfm_mbid:
                # Shortcut: lastfm_mbid IS the MusicBrainz ID — copy at 100% confidence
                conn.execute(
                    """
                    UPDATE lib_artists
                    SET musicbrainz_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND musicbrainz_id IS NULL
                    """,
                    (lastfm_mbid, artist_id),
                )
                write_enrichment_meta(
                    conn, "musicbrainz_id", "artist", artist_id,
                    "found", confidence=100,
                )
                enriched += 1
                logger.debug(
                    "enrich_artist_ids_musicbrainz: '%s' -> shortcut from lastfm_mbid=%s",
                    artist_name, lastfm_mbid,
                )
            else:
                # Full search path: query MusicBrainz API
                mbid = _search_musicbrainz(conn, artist_id, artist_name)
                if mbid:
                    enriched += 1
                else:
                    skipped += 1

            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))

        except Exception as e:
            logger.warning("enrich_artist_ids_musicbrainz: failed for '%s': %s", artist_name, e)
            write_enrichment_meta(
                conn, "musicbrainz_id", "artist", artist_id,
                "error", error_msg=str(e)[:200],
            )
            failed += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

    try:
        with _connect() as conn:
            remaining_row = conn.execute(
                """
                SELECT COUNT(*) FROM lib_artists
                WHERE musicbrainz_id IS NULL
                  AND removed_at IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'musicbrainz_id'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info(
        "enrich_artist_ids_musicbrainz: enriched=%d, skipped=%d, failed=%d, remaining=%d",
        enriched, skipped, failed, remaining,
    )
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}


def _search_musicbrainz(conn, artist_id: str, artist_name: str) -> str | None:
    """Search MusicBrainz by name with album-overlap validation.

    Returns the MBID if found, None otherwise.
    """
    from app.clients.musicbrainz_client import search_artist, get_artist_release_groups
    from app.services.enrichment._helpers import name_similarity_bonus
    from app.clients.music_client import norm

    lib_titles = [
        strip_title_suffixes(r["local_title"] or r["title"])
        for r in conn.execute(
            "SELECT title, local_title FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL",
            (artist_id,),
        ).fetchall()
    ]

    candidates = search_artist(artist_name, limit=5)
    if not candidates:
        write_enrichment_meta(conn, "musicbrainz_id", "artist", artist_id, "not_found", confidence=0)
        return None

    norm_name = norm(artist_name)
    best_mbid = None
    best_score = -1
    best_confidence = 0

    for cand in candidates[:3]:
        cand_name = cand.get("name", "")
        mbid = cand.get("mbid", "")
        if not mbid:
            continue

        nb = name_similarity_bonus(norm_name, norm(cand_name))
        if nb == 0:
            continue

        catalog_titles = get_artist_release_groups(mbid, limit=50)
        overlap = sum(
            1 for lt in lib_titles
            if any(match_album_title(lt, ct) >= 0.82 for ct in catalog_titles)
        )

        score = nb + (overlap * 300)
        if score > best_score:
            best_score = score
            best_mbid = mbid
            if overlap == 0:
                best_confidence = 70
            elif overlap <= 2:
                best_confidence = 85
            else:
                best_confidence = 95

    if best_mbid and best_confidence >= 70:
        needs_verification = 1 if best_confidence < 85 else 0
        conn.execute(
            """
            UPDATE lib_artists
            SET musicbrainz_id = ?,
                needs_verification = CASE WHEN ? = 1 THEN 1 ELSE needs_verification END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND musicbrainz_id IS NULL
            """,
            (best_mbid, needs_verification, artist_id),
        )
        write_enrichment_meta(
            conn, "musicbrainz_id", "artist", artist_id,
            "found", confidence=best_confidence,
        )
        logger.debug(
            "enrich_artist_ids_musicbrainz: '%s' -> mbid=%s conf=%d (search)",
            artist_name, best_mbid, best_confidence,
        )
        return best_mbid
    else:
        write_enrichment_meta(conn, "musicbrainz_id", "artist", artist_id, "not_found", confidence=0)
        return None
