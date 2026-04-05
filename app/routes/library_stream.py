"""
library_stream.py — Audio streaming proxy for the Rythmx player.

Exposes a single endpoint:
  GET /library/tracks/{track_id}/stream

The browser's <audio> element cannot send custom headers (X-Api-Key), so auth
is accepted as a query parameter (?api_key=...) on this endpoint only.
The API key is validated server-side; the upstream platform URL and credentials
are never exposed to the client.

Supports HTTP Range requests for seeking. The proxy forwards Range headers to
the upstream platform and relays partial-content (206) responses.

Platform dispatch:
  - navidrome: proxies via NavidromeClient.stream_response(song_id)
               song_id = lib_tracks.id (the Navidrome native song ID)
  - plex:      redirects to the Plex stream URL (includes token as query param)
               the browser follows the redirect directly to Plex
  - file:      serves directly from MUSIC_DIR/file_path (if MUSIC_DIR is set)
"""
import hmac
import logging
import mimetypes
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.db import rythmx_store
from app.db.rythmx_store import _connect

logger = logging.getLogger(__name__)

router = APIRouter()

_GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}


def _verify_key(api_key: str) -> None:
    """Validate the api_key query param against the stored key."""
    stored = rythmx_store.get_api_key() or ""
    if not hmac.compare_digest(api_key, stored):
        raise HTTPException(status_code=401, detail={"status": "error", "message": "Unauthorized"})


def _get_track(track_id: str) -> dict:
    """Fetch a single lib_tracks row by id. Raises 404 if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, source_platform, file_path, container, codec FROM lib_tracks "
            "WHERE id = ? AND removed_at IS NULL LIMIT 1",
            (track_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"status": "error", "message": "Track not found"})
    return dict(row)


def _is_m4a_track(track: dict) -> bool:
    """Best-effort m4a detection from path/container/codec metadata."""
    file_path = str(track.get("file_path") or "").lower()
    if file_path.endswith(".m4a"):
        return True

    container = str(track.get("container") or "").lower()
    codec = str(track.get("codec") or "").lower()
    return container in {"m4a", "mp4"} or codec in {"aac", "alac"}


def _resolve_content_type(track: dict, upstream_content_type: str | None) -> str:
    """Prefer upstream type, but map generic m4a responses to audio/mp4."""
    cleaned = str(upstream_content_type or "").split(";")[0].strip().lower()
    if cleaned not in _GENERIC_CONTENT_TYPES:
        return str(upstream_content_type)
    if _is_m4a_track(track):
        return "audio/mp4"
    return "audio/mpeg"


@router.get("/library/tracks/{track_id}/stream")
def stream_track(
    track_id: str,
    request: Request,
    api_key: str = Query(default=""),
):
    """Proxy or redirect to the audio stream for the given track.

    Auth: api_key query param (because <audio src> cannot send headers).
    Range requests: forwarded to upstream where supported.
    """
    _verify_key(api_key)
    track = _get_track(track_id)
    platform = track["source_platform"]

    # ------------------------------------------------------------------
    # Navidrome — proxy the stream through Rythmx (hides credentials)
    # ------------------------------------------------------------------
    if platform == "navidrome":
        from app.db.navidrome_reader import _get_client
        try:
            client = _get_client()
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail={"status": "error", "message": str(exc)},
            ) from exc

        # song_id for Navidrome is the lib_tracks.id value (e.g. "tr-abc123")
        song_id = track_id

        # Forward seek headers. For m4a, request byte ranges even if the
        # browser did not send one so startup/seek behavior remains stable.
        range_header = request.headers.get("range")
        if not range_header and _is_m4a_track(track):
            range_header = "bytes=0-"

        headers = {}
        if range_header:
            headers["Range"] = range_header
        if_range = request.headers.get("if-range")
        if if_range:
            headers["If-Range"] = if_range

        import requests as req_lib
        upstream_url = client.get_stream_url(song_id)
        try:
            upstream = req_lib.get(upstream_url, headers=headers, stream=True, timeout=30)
            upstream.raise_for_status()
        except req_lib.RequestException as exc:
            logger.warning("stream_track: upstream fetch failed for %s: %s", track_id, type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"status": "error", "message": "Upstream stream unavailable"},
            ) from exc

        content_type = _resolve_content_type(track, upstream.headers.get("Content-Type"))
        status_code = upstream.status_code  # 200 or 206

        def _iter():
            try:
                for chunk in upstream.iter_content(chunk_size=32768):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        response_headers = {
            "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        }
        for header in ("Content-Range", "Content-Length", "ETag", "Last-Modified", "Cache-Control"):
            if header in upstream.headers:
                response_headers[header] = upstream.headers[header]

        return StreamingResponse(
            _iter(),
            status_code=status_code,
            media_type=content_type,
            headers=response_headers,
        )

    # ------------------------------------------------------------------
    # Plex — redirect to the Plex stream URL (browser follows directly)
    # ------------------------------------------------------------------
    if platform == "plex":
        from app import config
        if not config.PLEX_URL or not config.PLEX_TOKEN:
            raise HTTPException(
                status_code=503,
                detail={"status": "error", "message": "Plex not configured"},
            )
        try:
            from plexapi.server import PlexServer
            plex = PlexServer(config.PLEX_URL, config.PLEX_TOKEN)
            # track_id for Plex is the ratingKey (numeric string)
            track = plex.fetchItem(int(track_id))
            stream_url = track.getStreamURL()
        except Exception as exc:
            logger.warning("stream_track: Plex stream URL failed for %s: %s", track_id, type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"status": "error", "message": "Could not resolve Plex stream URL"},
            ) from exc
        return RedirectResponse(url=stream_url, status_code=302)

    # ------------------------------------------------------------------
    # File — serve directly from MUSIC_DIR
    # ------------------------------------------------------------------
    if platform == "file":
        from app.config import MUSIC_DIR
        if not MUSIC_DIR:
            raise HTTPException(
                status_code=503,
                detail={"status": "error", "message": "MUSIC_DIR not configured"},
            )
        file_path = track.get("file_path") or ""
        abs_path = os.path.join(MUSIC_DIR, file_path.lstrip("/"))
        if not os.path.isfile(abs_path):
            raise HTTPException(
                status_code=404,
                detail={"status": "error", "message": "File not found"},
            )
        media_type, _ = mimetypes.guess_type(abs_path)
        return FileResponse(abs_path, media_type=media_type or "audio/mpeg")

    raise HTTPException(
        status_code=422,
        detail={"status": "error", "message": f"Unsupported platform: {platform}"},
    )
