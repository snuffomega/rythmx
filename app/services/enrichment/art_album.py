"""
art_album.py - Stage 1.2 album artwork enrichment.

Two independent passes:
  enrich_album_art_local()  — Stage 1.2a: embedded/sidecar only, zero network
  enrich_album_art_cdn()    — Stage 1.2b: Deezer/iTunes/Plex URLs, runs after Stage 2a

Writes image_cache rows with content_hash/local_path/artwork_source.
"""
from __future__ import annotations

import logging
import os

import requests

from app import config
from app.db.rythmx_store import _connect
from app.services.api_orchestrator import rate_limiter
from app.services.artwork_store import get_original_path, get_thumb, get_thumb_cache_path, ingest
from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta
from app.services.local_path_resolver import resolve_library_file_path

logger = logging.getLogger(__name__)

_LOCAL_CANDIDATE_SQL = """
    SELECT al.id,
           al.title,
           ar.name AS artist_name,
           al.source_platform,
           al.thumb_url_deezer,
           (SELECT t.file_path
              FROM lib_tracks t
             WHERE t.album_id = al.id
               AND t.file_path IS NOT NULL
               AND t.removed_at IS NULL
             ORDER BY t.track_number
             LIMIT 1) AS sample_file_path
    FROM lib_albums al
    JOIN lib_artists ar ON ar.id = al.artist_id
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    WHERE al.removed_at IS NULL
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
"""

_LOCAL_REMAINING_SQL = """
    SELECT COUNT(*)
    FROM lib_albums al
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    WHERE al.removed_at IS NULL
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
"""

_CDN_CANDIDATE_SQL = """
    SELECT al.id,
           al.title,
           ar.name AS artist_name,
           al.source_platform,
           al.thumb_url_deezer,
           al.thumb_url_plex,
           al.itunes_album_id,
           al.deezer_id,
           (SELECT t.file_path
              FROM lib_tracks t
             WHERE t.album_id = al.id
               AND t.file_path IS NOT NULL
               AND t.removed_at IS NULL
             ORDER BY t.track_number
             LIMIT 1) AS sample_file_path
    FROM lib_albums al
    JOIN lib_artists ar ON ar.id = al.artist_id
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    LEFT JOIN enrichment_meta em
           ON em.source      = 'album_art_cdn'
          AND em.entity_type = 'album'
          AND em.entity_id   = al.id
    WHERE al.removed_at IS NULL
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
      AND (
          em.status IS NULL
          OR em.status = 'error'
          OR (em.status = 'not_found'
              AND (em.retry_after IS NULL OR em.retry_after <= date('now')))
      )
"""

_CDN_REMAINING_SQL = """
    SELECT COUNT(*)
    FROM lib_albums al
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    LEFT JOIN enrichment_meta em
           ON em.source      = 'album_art_cdn'
          AND em.entity_type = 'album'
          AND em.entity_id   = al.id
    WHERE al.removed_at IS NULL
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
      AND (
          em.status IS NULL
          OR em.status = 'error'
          OR (em.status = 'not_found'
              AND (em.retry_after IS NULL OR em.retry_after <= date('now')))
      )
"""

_LOCAL_SYNC_CANDIDATE_SQL = """
    SELECT al.id,
           al.title,
           ar.name AS artist_name,
           al.source_platform,
           al.thumb_url_deezer,
           (SELECT t.file_path
              FROM lib_tracks t
             WHERE t.album_id = al.id
               AND t.file_path IS NOT NULL
               AND t.removed_at IS NULL
             ORDER BY t.track_number
             LIMIT 1) AS sample_file_path
    FROM lib_albums al
    JOIN lib_artists ar ON ar.id = al.artist_id
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    WHERE al.removed_at IS NULL
      AND al.source_platform = 'navidrome'
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
      AND EXISTS (
            SELECT 1
            FROM lib_tracks t
            WHERE t.album_id = al.id
              AND t.file_path IS NOT NULL
              AND t.removed_at IS NULL
      )
"""

_LOCAL_SYNC_REMAINING_SQL = """
    SELECT COUNT(*)
    FROM lib_albums al
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    WHERE al.removed_at IS NULL
      AND al.source_platform = 'navidrome'
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
      AND EXISTS (
            SELECT 1
            FROM lib_tracks t
            WHERE t.album_id = al.id
              AND t.file_path IS NOT NULL
              AND t.removed_at IS NULL
      )
"""

_session = requests.Session()
_session.headers["User-Agent"] = "Rythmx/1.0"


def _download_image_bytes(url: str) -> bytes | None:
    if not url:
        return None
    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.content
    except requests.RequestException as exc:
        logger.debug("art_album: image download failed %s: %s", url[:80], exc)
        return None


def _extract_embedded_art(file_path: str) -> bytes | None:
    import mutagen

    try:
        audio = mutagen.File(file_path, easy=False)
    except Exception as exc:
        logger.debug("art_album: mutagen read failed for %s: %s", file_path, exc)
        return None

    if audio is None:
        return None

    try:
        pictures = getattr(audio, "pictures", None)
        if pictures:
            front = next((p for p in pictures if getattr(p, "type", None) == 3), pictures[0])
            data = getattr(front, "data", None)
            if data:
                return data
    except Exception:
        pass

    try:
        tags = getattr(audio, "tags", None)
        if tags is not None and hasattr(tags, "getall"):
            apic_frames = tags.getall("APIC")
            if apic_frames:
                front = next((f for f in apic_frames if getattr(f, "type", None) == 3), apic_frames[0])
                data = getattr(front, "data", None)
                if data:
                    return data
    except Exception:
        pass

    return None


def _extract_sidecar_art(file_path: str) -> bytes | None:
    """
    Look for common album sidecar artwork files in the track's directory.

    This is the local-file fallback when tracks do not embed APIC/PICTURE bytes.
    """
    folder = os.path.dirname(file_path)
    if not folder:
        return None

    candidates = (
        "cover.jpg",
        "cover.jpeg",
        "cover.png",
        "folder.jpg",
        "folder.jpeg",
        "folder.png",
        "front.jpg",
        "front.jpeg",
        "front.png",
    )

    for name in candidates:
        candidate = os.path.join(folder, name)
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "rb") as fh:
                payload = fh.read()
            if payload:
                return payload
        except OSError as exc:
            logger.debug("art_album: failed reading sidecar art %s: %s", candidate, exc)
    return None


def _extract_local_album_art(
    source_platform: str,
    sample_file_path: str | None,
    *,
    artist_name: str | None = None,
    album_title: str | None = None,
) -> tuple[bytes | None, str]:
    """
    Return (payload, source) from local files for navidrome-backed albums.
    """
    if source_platform != "navidrome" or not config.MUSIC_DIR or not sample_file_path:
        return None, ""

    abs_path, mode = resolve_library_file_path(
        config.MUSIC_DIR,
        str(sample_file_path),
        artist_name=artist_name,
        album_title=album_title,
    )
    if not abs_path:
        return None, ""
    if mode == "fallback":
        logger.debug("art_album: path fallback resolved '%s' (%s)", sample_file_path, album_title or "")

    payload = _extract_embedded_art(abs_path)
    if payload:
        return payload, "embedded"

    payload = _extract_sidecar_art(abs_path)
    if payload:
        return payload, "embedded"

    return None, ""


def _itunes_artwork_url(itunes_album_id: str) -> str:
    if not itunes_album_id:
        return ""

    rate_limiter.acquire("itunes")
    try:
        resp = _session.get(
            "https://itunes.apple.com/lookup",
            params={"id": itunes_album_id, "entity": "collection", "limit": 1},
            timeout=12,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("itunes")
            return ""
        resp.raise_for_status()
        rate_limiter.record_success("itunes")
        data = resp.json()
        for item in data.get("results", []):
            raw = item.get("artworkUrl100", "")
            if raw:
                return raw.replace("100x100bb", "600x600bb")
    except (requests.RequestException, ValueError) as exc:
        logger.debug("art_album: itunes lookup failed for %s: %s", itunes_album_id, exc)
    return ""


def _download_plex_thumb(thumb_url_plex: str) -> tuple[bytes | None, str]:
    if not thumb_url_plex or not config.PLEX_URL or not config.PLEX_TOKEN:
        return None, ""

    base = config.PLEX_URL.rstrip("/")
    path = thumb_url_plex if thumb_url_plex.startswith("/") else f"/{thumb_url_plex}"
    joiner = "&" if "?" in path else "?"
    url = f"{base}{path}{joiner}X-Plex-Token={config.PLEX_TOKEN}"
    return _download_image_bytes(url), thumb_url_plex


def _upsert_album_cache(
    conn,
    *,
    album_id: str,
    image_url: str | None,
    content_hash: str,
    artwork_source: str,
) -> None:
    conn.execute(
        """INSERT INTO image_cache
               (entity_type, entity_key, image_url, local_path, content_hash, artwork_source, last_accessed)
           VALUES ('album', ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(entity_type, entity_key) DO UPDATE SET
               image_url=excluded.image_url,
               local_path=excluded.local_path,
               content_hash=excluded.content_hash,
               artwork_source=excluded.artwork_source,
               last_accessed=datetime('now')""",
        (album_id, image_url, str(get_original_path(content_hash)), content_hash, artwork_source),
    )


def _process_local_item(conn, row):
    album_id = str(row["id"])
    album_title = row["title"]
    artist_name = row["artist_name"]
    source_platform = row["source_platform"]
    thumb_url_deezer = row["thumb_url_deezer"]
    sample_file_path = row["sample_file_path"]

    payload, source = _extract_local_album_art(
        str(source_platform or ""),
        sample_file_path,
        artist_name=artist_name,
        album_title=album_title,
    )

    if payload:
        content_hash = ingest(payload)
        _upsert_album_cache(
            conn,
            album_id=album_id,
            image_url=str(thumb_url_deezer) if thumb_url_deezer else None,
            content_hash=content_hash,
            artwork_source=source,
        )
        write_enrichment_meta(conn, "album_art_local", "album", album_id, "found")
        logger.debug("art_album local: '%s - %s' -> %s", artist_name, album_title, source)
        return "found"

    # Write not_found WITHOUT retry_after so CDN pass runs immediately.
    try:
        conn.execute(
            """INSERT OR REPLACE INTO enrichment_meta
                   (source, entity_type, entity_id, status, enriched_at, error_msg)
               VALUES ('album_art_local', 'album', ?, 'not_found', CURRENT_TIMESTAMP,
                       'no_local_art')""",
            (album_id,),
        )
    except Exception:
        pass
    logger.debug("art_album local: '%s - %s' -> not found (no local art)", artist_name, album_title)
    return "not_found"


def _process_cdn_item(conn, row):
    album_id = str(row["id"])
    album_title = row["title"]
    artist_name = row["artist_name"]
    source_platform = row["source_platform"]
    thumb_url_deezer = row["thumb_url_deezer"]
    thumb_url_plex = row["thumb_url_plex"]
    itunes_album_id = row["itunes_album_id"]
    deezer_id = row["deezer_id"]

    has_metadata_match = bool(deezer_id) or bool(itunes_album_id)

    payload: bytes | None = None
    source = ""
    source_url: str | None = None

    # Priority 1: Deezer cover URL — only if Deezer match confirmed
    if payload is None and has_metadata_match and thumb_url_deezer:
        payload = _download_image_bytes(str(thumb_url_deezer))
        if payload:
            source = "deezer"
            source_url = str(thumb_url_deezer)

    # Priority 2: iTunes lookup — only if iTunes match confirmed
    if payload is None and has_metadata_match and itunes_album_id:
        itunes_url = _itunes_artwork_url(str(itunes_album_id))
        if itunes_url:
            payload = _download_image_bytes(itunes_url)
            if payload:
                source = "itunes"
                source_url = itunes_url

    # Priority 3: Plex thumb — available without external IDs
    if payload is None and source_platform == "plex" and thumb_url_plex:
        payload, plex_ref = _download_plex_thumb(str(thumb_url_plex))
        if payload:
            source = "plex"
            source_url = plex_ref

    if payload:
        content_hash = ingest(payload)
        _upsert_album_cache(
            conn,
            album_id=album_id,
            image_url=source_url or (str(thumb_url_deezer) if thumb_url_deezer else None),
            content_hash=content_hash,
            artwork_source=source,
        )
        write_enrichment_meta(conn, "album_art_cdn", "album", album_id, "found")
        logger.debug(
            "art_album cdn: '%s - %s' -> %s", artist_name, album_title, source
        )
        return "found"

    write_enrichment_meta(conn, "album_art_cdn", "album", album_id, "not_found")
    logger.debug(
        "art_album cdn: '%s - %s' -> not found (match=%s)",
        artist_name, album_title, has_metadata_match,
    )
    return "not_found"


def enrich_album_art_local(batch_size: int = 2000, stop_event=None, on_progress=None):
    """Stage 1.2a: zero-network local file art pass. Runs before Stage 2a."""
    return run_enrichment_loop(
        worker_name="enrich_album_art_local",
        candidate_sql=_LOCAL_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_LOCAL_REMAINING_SQL,
        remaining_params=(),
        source="album_art_local",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_local_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )


def enrich_album_art_cdn(batch_size: int = 200, stop_event=None, on_progress=None):
    """Stage 1.2b: CDN URL pass. Runs after Stage 2a (IDs available).
    Skips albums with no metadata match. Respects 30-day enrichment_meta gate."""
    return run_enrichment_loop(
        worker_name="enrich_album_art_cdn",
        candidate_sql=_CDN_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_CDN_REMAINING_SQL,
        remaining_params=(),
        source="album_art_cdn",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_cdn_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )


def hydrate_local_album_art_after_sync(batch_size: int = 2000) -> dict:
    """
    Fast local-only pass used immediately after library sync.

    This runs before full Stage 1.2 so embedded/sidecar art is available as early
    as possible without waiting for remote artwork lookups.
    """
    if not config.MUSIC_DIR:
        return {"processed": 0, "enriched": 0, "skipped": 0, "remaining": 0}

    with _connect() as conn:
        rows = conn.execute(
            _LOCAL_SYNC_CANDIDATE_SQL + " LIMIT ?",
            (batch_size,),
        ).fetchall()

        enriched = 0
        skipped = 0
        for row in rows:
            album_id = str(row["id"])
            source_platform = str(row["source_platform"] or "")
            sample_file_path = row["sample_file_path"]
            thumb_url_deezer = row["thumb_url_deezer"]

            payload, source = _extract_local_album_art(
                source_platform,
                sample_file_path,
                artist_name=str(row["artist_name"] or ""),
                album_title=str(row["title"] or ""),
            )
            if not payload:
                skipped += 1
                continue

            content_hash = ingest(payload)
            _upsert_album_cache(
                conn,
                album_id=album_id,
                image_url=str(thumb_url_deezer) if thumb_url_deezer else None,
                content_hash=content_hash,
                artwork_source=source,
            )
            enriched += 1

        remaining = conn.execute(_LOCAL_SYNC_REMAINING_SQL).fetchone()[0]
        conn.commit()

    return {
        "processed": len(rows),
        "enriched": enriched,
        "skipped": skipped,
        "remaining": remaining,
    }


def prewarm_album_art_cache(size: int = 300, limit: int = 1000) -> dict:
    """
    Generate cached WebP thumbnails for album hashes missing a size-specific cache file.
    """
    warmed = 0
    errors = 0

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT content_hash
            FROM image_cache
            WHERE entity_type = 'album'
              AND content_hash IS NOT NULL
              AND content_hash != ''
            ORDER BY last_accessed DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    for row in rows:
        content_hash = str(row["content_hash"])
        cache_path = get_thumb_cache_path(content_hash, size)
        if cache_path.exists():
            continue
        try:
            get_thumb(content_hash, size=size)
            warmed += 1
        except Exception as exc:
            errors += 1
            logger.debug("art_album prewarm failed for %s: %s", content_hash, exc)

    return {"candidates": len(rows), "warmed": warmed, "errors": errors}
