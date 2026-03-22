"""
id_spotify.py — Stage 2 Spotify ID worker + Stage 3 wrapper + status.

enrich_artist_ids_spotify(): validate + store spotify_artist_id only.
enrich_spotify(): thin wrapper — runs Stage 2 then Stage 3.
get_spotify_status(): UI status for Settings page.
"""
import logging
import threading

from app import config
from app.db import rythmx_store
from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.enrichment._helpers import (
    strip_title_suffixes,
    match_album_title,
    name_similarity_bonus,
)
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)


def enrich_artist_ids_spotify(batch_size: int = 20, stop_event: threading.Event | None = None,
                               on_progress: "callable | None" = None) -> dict:
    """
    Stage 2 — Spotify ID Worker: validate + store spotify_artist_id only.
    No rich data (genres, popularity) — those belong in Stage 3 (enrich_genres_spotify).
    Optional: gracefully skips if SPOTIFY_CLIENT_ID/SECRET not configured.
    """
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1,
                "error": "Spotify credentials not configured"}

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
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
            conn = _connect()
        except Exception:
            failed += 1
            continue

        try:
            from app.clients.music_client import norm

            lib_titles = [
                strip_title_suffixes(r["local_title"] or r["title"])
                for r in conn.execute(
                    "SELECT title, local_title FROM lib_albums WHERE artist_id = ? AND removed_at IS NULL",
                    (artist_id,),
                ).fetchall()
            ]

            rate_limiter.acquire("spotify")
            results = sp.search(q=f'artist:"{artist_name}"', type="artist", limit=5)
            items = results.get("artists", {}).get("items", [])

            if not items:
                write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
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
                nb = name_similarity_bonus(norm_name, norm(candidate["name"]))
                if nb == 0:
                    continue
                rate_limiter.acquire("spotify")
                albums_resp = sp.artist_albums(
                    candidate["id"], include_groups="album,single", limit=50
                )
                catalog_titles = [a["name"] for a in albums_resp.get("items", [])]
                overlap = sum(
                    1 for lt in lib_titles
                    if any(match_album_title(lt, ct) >= 0.82 for ct in catalog_titles)
                )
                score = nb + (overlap * 300)
                if score > best_score:
                    best_score = score
                    best_candidate = candidate
                    best_conf = 95 if overlap >= 3 else (85 if overlap >= 1 else 70)

            if best_candidate is None:
                write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                      "not_found", confidence=0)
                skipped += 1
                if on_progress:
                    on_progress(enriched, skipped, failed, len(rows))
                continue

            needs_verification = 1 if best_conf < 85 else 0
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
            write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                  "found", confidence=best_conf)
            enriched += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
            logger.debug("enrich_artist_ids_spotify: '%s' -> id=%s conf=%d",
                         artist_name, best_candidate["id"], best_conf)

        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("enrich_artist_ids_spotify: rate limit hit on '%s' — stopping", artist_name)
                break
            logger.warning("enrich_artist_ids_spotify: failed for '%s': %s", artist_name, e)
            write_enrichment_meta(conn, "spotify_id", "artist", artist_id,
                                  "error", error_msg=str(e)[:200])
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


def enrich_spotify(batch_size: int = 20) -> dict:
    """Thin wrapper — runs Stage 2 (ID resolution) then Stage 3 (genres/popularity)."""
    from app.services.enrichment.rich_spotify import enrich_genres_spotify
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
            total = conn.execute("SELECT COUNT(*) FROM lib_artists").fetchone()[0]
            enriched = conn.execute(
                "SELECT COUNT(*) FROM lib_artists WHERE spotify_artist_id IS NOT NULL"
            ).fetchone()[0]
    except Exception:
        total = enriched = 0

    last_run = rythmx_store.get_setting("spotify_enrich_last_run")
    return {
        "enriched_artists": enriched,
        "total_artists": total,
        "last_run": last_run,
        "spotify_available": bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET),
    }
