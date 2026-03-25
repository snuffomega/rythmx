"""
rich_spotify.py — Stage 3 Spotify genres + popularity worker.

Requires: spotify_artist_id stored by enrich_artist_ids_spotify() (Stage 2).
Fetches: genres_json_spotify, popularity_spotify, appears_on albums, raw cache.
"""
import json
import logging
import threading

from app import config
from app.db.rythmx_store import _connect
from app.services.enrichment._base import write_enrichment_meta
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)


def enrich_genres_spotify(batch_size: int = 20, stop_event: threading.Event | None = None,
                           on_progress: "callable | None" = None) -> dict:
    """
    Stage 3 — Spotify genres + popularity worker.
    Requires: spotify_artist_id (Stage 2).
    Fetches: genres_json, popularity, appears_on albums, raw cache.
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
        logger.error("enrich_genres_spotify: Spotify client init failed: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    enriched = 0
    skipped = 0
    failed = 0

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, spotify_artist_id FROM lib_artists
                WHERE spotify_artist_id IS NOT NULL
                  AND genres_json_spotify IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_genres'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as e:
        logger.error("enrich_genres_spotify: could not read lib_artists: %s", e)
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": -1, "error": str(e)}

    if not rows:
        return {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    for artist in rows:
        if stop_event and stop_event.is_set():
            break
        artist_id = artist["id"]
        artist_name = artist["name"]
        spotify_artist_id = artist["spotify_artist_id"]

        try:
            conn = _connect()
        except Exception:
            failed += 1
            continue

        try:
            rate_limiter.acquire("spotify")
            artist_data = sp.artist(spotify_artist_id)

            rate_limiter.acquire("spotify")
            appears_on_data = sp.artist_albums(
                spotify_artist_id, include_groups="appears_on", limit=20
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO spotify_raw_cache
                    (query_type, entity_id, entity_name, raw_json, fetched_at)
                VALUES ('artist', ?, ?, ?, datetime('now'))
                """,
                (spotify_artist_id, artist_name, json.dumps(artist_data)),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO spotify_raw_cache
                    (query_type, entity_id, entity_name, raw_json, fetched_at)
                VALUES ('appears_on', ?, ?, ?, datetime('now'))
                """,
                (spotify_artist_id, artist_name, json.dumps(appears_on_data)),
            )
            genres_json = json.dumps(artist_data.get("genres", []))
            popularity = artist_data.get("popularity")
            conn.execute(
                """
                UPDATE lib_artists
                SET genres_json_spotify = COALESCE(?, genres_json_spotify),
                    popularity_spotify  = COALESCE(?, popularity_spotify),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (genres_json, popularity, artist_id),
            )
            write_enrichment_meta(conn, "spotify_genres", "artist", artist_id, "found")
            enriched += 1
            if on_progress:
                on_progress(enriched, skipped, failed, len(rows))
            logger.debug("enrich_genres_spotify: '%s' -> genres=%s popularity=%s",
                         artist_name, artist_data.get("genres", [])[:3], popularity)

        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("enrich_genres_spotify: rate limit hit on '%s' — stopping", artist_name)
                break
            logger.warning("enrich_genres_spotify: failed for '%s': %s", artist_name, e)
            write_enrichment_meta(conn, "spotify_genres", "artist", artist_id,
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
                WHERE spotify_artist_id IS NOT NULL
                  AND genres_json_spotify IS NULL
                  AND id NOT IN (
                      SELECT entity_id FROM enrichment_meta
                      WHERE entity_type = 'artist' AND source = 'spotify_genres'
                        AND (status = 'found'
                             OR (status = 'not_found'
                                 AND (retry_after IS NULL OR retry_after > date('now'))))
                  )
                """
            ).fetchone()
            remaining = remaining_row[0] if remaining_row else -1
    except Exception:
        remaining = -1

    logger.info("enrich_genres_spotify: enriched=%d, skipped=%d, failed=%d, remaining=%d",
                enriched, skipped, failed, remaining)
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "remaining": remaining}
