"""
art_album.py - Stage 1.2 album artwork enrichment.

Priority order:
  1. Embedded artwork (APIC/PICTURE) for navidrome files when MUSIC_DIR is set
  2. Deezer CDN artwork
  3. iTunes lookup artwork
  4. Plex thumb download (token available at Stage 1 time)

Writes image_cache rows with content_hash/local_path/artwork_source.
"""
from __future__ import annotations

import logging
import os

import requests

from app import config
from app.db import rythmx_store
from app.db.rythmx_store import _connect
from app.services.api_orchestrator import rate_limiter
from app.services.artwork_store import get_original_path, get_thumb, get_thumb_cache_path, ingest
from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT al.id,
           al.title,
           ar.name AS artist_name,
           al.source_platform,
           al.thumb_url_deezer,
           al.thumb_url_plex,
           al.itunes_album_id,
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

_REMAINING_SQL = """
    SELECT COUNT(*)
    FROM lib_albums al
    LEFT JOIN image_cache ic
           ON ic.entity_type = 'album' AND ic.entity_key = al.id
    WHERE al.removed_at IS NULL
      AND (ic.content_hash IS NULL OR ic.content_hash = '')
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


def _process_item(conn, row):
    album_id = str(row["id"])
    album_title = row["title"]
    artist_name = row["artist_name"]
    source_platform = row["source_platform"]
    thumb_url_deezer = row["thumb_url_deezer"]
    thumb_url_plex = row["thumb_url_plex"]
    itunes_album_id = row["itunes_album_id"]
    sample_file_path = row["sample_file_path"]

    payload: bytes | None = None
    source = ""
    source_url: str | None = None

    # Priority 1: embedded artwork for local-file-backed navidrome libraries.
    if source_platform == "navidrome" and config.MUSIC_DIR and sample_file_path:
        abs_path = os.path.join(config.MUSIC_DIR, str(sample_file_path).lstrip("/\\"))
        if os.path.isfile(abs_path):
            payload = _extract_embedded_art(abs_path)
            if payload:
                source = "embedded"

    # Priority 2: Deezer cover URL from Stage 2a promotion.
    if payload is None and thumb_url_deezer:
        payload = _download_image_bytes(str(thumb_url_deezer))
        if payload:
            source = "deezer"
            source_url = str(thumb_url_deezer)

    # Priority 3: iTunes lookup by album ID (direct or via release registry).
    if payload is None:
        if not itunes_album_id:
            itunes_album_id = rythmx_store.get_release_itunes_album_id(artist_name, album_title)
        itunes_url = _itunes_artwork_url(str(itunes_album_id or ""))
        if itunes_url:
            payload = _download_image_bytes(itunes_url)
            if payload:
                source = "itunes"
                source_url = itunes_url

    # Priority 4: Plex thumb download with token while Stage 1 context is warm.
    if payload is None and source_platform == "plex" and thumb_url_plex:
        payload, plex_ref = _download_plex_thumb(str(thumb_url_plex))
        if payload:
            source = "plex"
            source_url = plex_ref

    if payload:
        content_hash = ingest(payload)
        fallback_url = source_url or (str(thumb_url_deezer) if thumb_url_deezer else None)
        _upsert_album_cache(
            conn,
            album_id=album_id,
            image_url=fallback_url,
            content_hash=content_hash,
            artwork_source=source,
        )
        write_enrichment_meta(conn, "album_art", "album", album_id, "found")
        logger.debug("art_album: '%s - %s' -> %s", artist_name, album_title, source)
        return "found"

    write_enrichment_meta(conn, "album_art", "album", album_id, "not_found")
    logger.debug("art_album: '%s - %s' -> not found", artist_name, album_title)
    return "not_found"


def enrich_album_art(batch_size=200, stop_event=None, on_progress=None):
    """Stage 1.2: album artwork local storage pass."""
    return run_enrichment_loop(
        worker_name="enrich_album_art",
        candidate_sql=_CANDIDATE_SQL,
        candidate_params=(),
        remaining_sql=_REMAINING_SQL,
        remaining_params=(),
        source="album_art",
        entity_type="album",
        entity_id_col="id",
        process_item=_process_item,
        batch_size=batch_size,
        stop_event=stop_event,
        on_progress=on_progress,
    )


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
