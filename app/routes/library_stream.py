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


def _verify_key(api_key: str) -> None:
    """Validate the api_key query param against the stored key."""
    stored = rythmx_store.get_api_key() or ""
    if not hmac.compare_digest(api_key, stored):
        raise HTTPException(status_code=401, detail={"status": "error", "message": "Unauthorized"})


def _get_track(track_id: str) -> dict:
    """Fetch a single lib_tracks row by id. Raises 404 if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, source_platform, file_path FROM lib_tracks "
            "WHERE id = ? AND removed_at IS NULL LIMIT 1",
            (track_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"status": "error", "message": "Track not found"})
    return dict(row)


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

        # Forward Range header if present (enables seeking)
        range_header = request.headers.get("range")
        headers = {}
        if range_header:
            headers["Range"] = range_header

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

        content_type = upstream.headers.get("Content-Type", "audio/mpeg")
        status_code = upstream.status_code  # 200 or 206

        def _iter():
            try:
                for chunk in upstream.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        response_headers = {"Accept-Ranges": "bytes"}
        if "Content-Range" in upstream.headers:
            response_headers["Content-Range"] = upstream.headers["Content-Range"]
        if "Content-Length" in upstream.headers:
            response_headers["Content-Length"] = upstream.headers["Content-Length"]

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
