"""
id_itunes_deezer.py — Stage 2 primary ID workers: iTunes + Deezer artist-first confidence loop.

enrich_library(): for each artist batch, validate via album catalog overlap,
then match individual albums against pre-fetched catalogs.
"""
import logging
import threading

from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.enrichment._helpers import (
    strip_title_suffixes,
    match_album_title,
    validate_artist,
    persist_artist_catalog,
)

logger = logging.getLogger(__name__)


def enrich_library(batch_size: int = 50, stop_event: threading.Event | None = None,
                    on_progress: "callable | None" = None) -> dict:
    """
    Stage 2 — Primary ID Workers: artist-first confidence loop for iTunes + Deezer.

    Batches by artist (not album). For each artist:
      FAST PATH  — stored artist ID at confidence ≥ 85: skip validation, fetch catalog directly.
      VALIDATION — no stored ID: run validate_artist() for iTunes + Deezer independently.
                   Both always run — writes both itunes_album_id AND deezer_id when found.

    Album matching uses pre-fetched catalog + match_album_title() threshold ≥ 0.82.

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

        # One connection per artist — all reads + writes inside this block
        try:
            conn = _connect()
        except Exception as e:
            logger.warning("enrich_library: could not open connection for '%s': %s", artist_name, e)
            failed += 1
            continue

        try:
            # Load this artist's albums that still need IDs
            album_rows = conn.execute(
                """
                SELECT id, title, local_title, itunes_album_id, deezer_id
                FROM lib_albums
                WHERE artist_id = ? AND removed_at IS NULL
                  AND (itunes_album_id IS NULL OR deezer_id IS NULL)
                """,
                (artist_id,),
            ).fetchall()

            if not album_rows:
                continue

            lib_titles = [strip_title_suffixes(r["local_title"] or r["title"]) for r in album_rows]

            # --- iTunes: fast path or validation ---
            itunes_catalog: list[dict] = []
            itunes_artist_id = artist["itunes_artist_id"]

            if itunes_artist_id:
                from app.clients.music_client import get_artist_albums_itunes
                itunes_catalog = get_artist_albums_itunes(itunes_artist_id)
                logger.debug("enrich_library: iTunes fast path for '%s' (id=%s, %d albums)",
                             artist_name, itunes_artist_id, len(itunes_catalog))
            else:
                val = validate_artist(artist_name, lib_titles, "itunes")
                if val:
                    itunes_artist_id = val["artist_id"]
                    itunes_catalog = val["album_catalog"]
                    try:
                        conn.execute(
                            """
                            UPDATE lib_artists
                            SET itunes_artist_id = ?, match_confidence = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND itunes_artist_id IS NULL
                            """,
                            (itunes_artist_id, val["confidence"], artist_id),
                        )
                        write_enrichment_meta(conn, "itunes_artist", "artist", artist_id,
                                               "found", confidence=val["confidence"])
                        logger.debug(
                            "enrich_library: iTunes validated '%s' → id=%s conf=%d",
                            artist_name, itunes_artist_id, val["confidence"],
                        )
                    except Exception as e:
                        logger.warning("enrich_library: iTunes artist write failed for '%s': %s",
                                       artist_name, e)
                else:
                    write_enrichment_meta(conn, "itunes_artist", "artist", artist_id,
                                           "not_found")

            # --- Deezer: fast path or validation ---
            deezer_catalog: list[dict] = []
            deezer_artist_id = artist["deezer_artist_id"]

            if deezer_artist_id:
                from app.clients.music_client import get_artist_albums_deezer
                deezer_catalog = get_artist_albums_deezer(deezer_artist_id)
                logger.debug("enrich_library: Deezer fast path for '%s' (id=%s, %d albums)",
                             artist_name, deezer_artist_id, len(deezer_catalog))
            else:
                val = validate_artist(artist_name, lib_titles, "deezer")
                if val:
                    deezer_artist_id = val["artist_id"]
                    deezer_catalog = val["album_catalog"]
                    try:
                        conn.execute(
                            """
                            UPDATE lib_artists
                            SET deezer_artist_id = ?, match_confidence = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND deezer_artist_id IS NULL
                            """,
                            (deezer_artist_id, val["confidence"], artist_id),
                        )
                        write_enrichment_meta(conn, "deezer_artist", "artist", artist_id,
                                               "found", confidence=val["confidence"])
                    except Exception as e:
                        logger.warning("enrich_library: Deezer artist write failed for '%s': %s",
                                       artist_name, e)
                else:
                    write_enrichment_meta(conn, "deezer_artist", "artist", artist_id,
                                           "not_found")

            # --- Persist catalogs for gap analysis (missing-album hints) ---
            persist_artist_catalog(conn, artist_id, "itunes", itunes_catalog)
            persist_artist_catalog(conn, artist_id, "deezer", deezer_catalog)

            # --- Album matching against pre-fetched catalogs ---
            # Build rich lookup: title → {id, track_count, record_type}
            itunes_by_title = {c["title"]: c for c in itunes_catalog if c.get("title")}
            deezer_titles = {c["title"]: c.get("id", "") for c in deezer_catalog if c.get("title")}

            for album in album_rows:
                album_id = album["id"]
                raw_title = album["local_title"] or album["title"]
                album_title = strip_title_suffixes(raw_title)
                album_enriched = False

                # iTunes album match (with track-count tiebreaker)
                if album["itunes_album_id"] is None and itunes_by_title:
                    scored = []
                    for t, entry in itunes_by_title.items():
                        s = match_album_title(album_title, t)
                        if s >= 0.82:
                            scored.append((t, s, entry))

                    if scored:
                        # Primary: best title score. Tiebreaker: track count proximity.
                        lib_track_count = conn.execute(
                            "SELECT COUNT(*) FROM lib_tracks WHERE album_id = ?",
                            (album_id,),
                        ).fetchone()[0]

                        def _rank(candidate):
                            title, title_score, entry = candidate
                            # Primary: title similarity (higher is better)
                            rank = title_score * 10000
                            # Tiebreaker 1: track count proximity (lower diff = better)
                            api_tc = entry.get("track_count", 0)
                            if lib_track_count > 0 and api_tc > 0:
                                rank -= abs(lib_track_count - api_tc) * 10
                            # Tiebreaker 2: release type match (if library title hints at type)
                            raw_lower = raw_title.lower()
                            api_type = entry.get("record_type", "")
                            if ("[single]" in raw_lower or "(single)" in raw_lower) and api_type == "single":
                                rank += 50
                            elif ("[ep]" in raw_lower or "(ep)" in raw_lower) and api_type == "ep":
                                rank += 50
                            return rank

                        best_title, best_score, best_entry = max(scored, key=_rank)
                        matched_id = best_entry["id"]
                        try:
                            conn.execute(
                                """
                                UPDATE lib_albums
                                SET itunes_album_id = ?,
                                    api_title = ?,
                                    match_confidence = 90,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ? AND itunes_album_id IS NULL
                                """,
                                (matched_id, best_title, album_id),
                            )
                            write_enrichment_meta(conn, "itunes", "album", album_id,
                                                   "found", confidence=90)
                            album_enriched = True
                            logger.debug(
                                "enrich_library: iTunes album hit '%s / %s' → id=%s (score=%.2f, tracks=%d)",
                                artist_name, album_title, matched_id, best_score,
                                best_entry.get("track_count", 0),
                            )
                        except Exception as e:
                            logger.warning("enrich_library: iTunes album write failed '%s / %s': %s",
                                           artist_name, album_title, e)
                            failed += 1
                            if on_progress:
                                on_progress(enriched, skipped, failed, _total_pending)
                            continue
                    else:
                        write_enrichment_meta(conn, "itunes", "album", album_id,
                                               "not_found", confidence=0)

                # Deezer album match (always runs — not a fallback)
                if album["deezer_id"] is None and deezer_titles:
                    best_deezer = max(
                        ((t, match_album_title(album_title, t)) for t in deezer_titles),
                        key=lambda x: x[1],
                        default=(None, 0.0),
                    )
                    if best_deezer[1] >= 0.82:
                        matched_id = deezer_titles[best_deezer[0]]
                        try:
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
                            write_enrichment_meta(conn, "deezer", "album", album_id,
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
                        write_enrichment_meta(conn, "deezer", "album", album_id,
                                               "not_found", confidence=0)

                # Album with no artist catalog match at all → flag for review
                if not album_enriched and not itunes_by_title and not deezer_titles:
                    conn.execute(
                        """
                        UPDATE lib_albums
                        SET match_confidence = 0, needs_verification = 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (album_id,),
                    )
                    skipped += 1
                    if on_progress:
                        on_progress(enriched, skipped, failed, _total_pending)
                elif album_enriched:
                    enriched += 1
                    if on_progress:
                        on_progress(enriched, skipped, failed, _total_pending)
                else:
                    skipped += 1
                    if on_progress:
                        on_progress(enriched, skipped, failed, _total_pending)

        except Exception as e:
            logger.warning("enrich_library: failed processing artist '%s': %s", artist_name, e)
            failed += 1
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass

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
