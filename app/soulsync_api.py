"""
soulsync_api.py — HTTP client for SoulSync's REST API.

Confirmed endpoints (from rythmx_ui reference implementation):
  POST /api/download        — queue a track or album for download
  GET  /api/status          — health check
  GET  /api/download/queue  — current download queue
  GET  /api/download/{id}   — specific job status

track_name is required for track-level downloads.
For album-level (Cruise Control), pass artist_name + album_name only.
409 = already queued — treated as ok.
"""
import requests
import logging
from app import config

logger = logging.getLogger(__name__)

TIMEOUT = 15


def _url(path: str) -> str:
    return f"{config.SOULSYNC_URL.rstrip('/')}/{path.lstrip('/')}"


def queue_download(track: dict) -> dict:
    """
    Ask SoulSync to download a track or album.

    For track-level (Discovery page):
        track_name, artist_name, spotify_track_id, album_name

    For album-level (Cruise Control):
        artist_name, album_name, source_url, deezer_album_id, spotify_album_id
        (track_name omitted — SoulSync downloads full album)

    Returns {status: 'ok'|'error', ...}
    """
    payload = {"source": track.get("source", "rythmx")}

    if track.get("track_name"):
        payload["track_name"] = track["track_name"]
    if track.get("artist_name"):
        payload["artist_name"] = track["artist_name"]
    if track.get("album_name"):
        payload["album_name"] = track["album_name"]
    if track.get("spotify_track_id"):
        payload["spotify_track_id"] = track["spotify_track_id"]
    if track.get("spotify_album_id"):
        payload["spotify_album_id"] = track["spotify_album_id"]
    if track.get("deezer_album_id"):
        payload["deezer_album_id"] = track["deezer_album_id"]
    if track.get("source_url"):
        payload["source_url"] = track["source_url"]

    try:
        resp = requests.post(_url("/api/download"), json=payload, timeout=TIMEOUT)

        if resp.status_code == 409:
            # Already queued or already owned — not an error
            return {"status": "ok", "message": "already_queued"}

        resp.raise_for_status()

        data = {}
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
        return {"status": "ok", "data": data}

    except requests.HTTPError as e:
        logger.error("SoulSync download failed (HTTP %s): %s / %s",
                     e.response.status_code,
                     track.get("artist_name"), track.get("track_name") or track.get("album_name"))
        return {"status": "error", "message": str(e)}
    except requests.RequestException as e:
        logger.error("SoulSync unreachable: %s", e)
        return {"status": "error", "message": "SoulSync unreachable"}


def get_download_status(job_id: str = None) -> dict:
    """
    Get download queue status, or a specific job's status.
    Returns {status: 'ok', data: {...}} or {status: 'error', ...}
    """
    path = f"/api/download/{job_id}" if job_id else "/api/download/queue"
    try:
        resp = requests.get(_url(path), timeout=TIMEOUT)
        resp.raise_for_status()
        return {"status": "ok", "data": resp.json()}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def test_connection() -> dict:
    """
    Verify SoulSync is reachable.
    Returns {status: 'ok', url} or {status: 'error', message}
    """
    if not config.SOULSYNC_URL:
        return {"status": "error", "message": "SOULSYNC_URL not set"}

    try:
        resp = requests.get(_url("/api/status"), timeout=TIMEOUT)
        if resp.status_code == 200:
            return {"status": "ok", "url": config.SOULSYNC_URL}
        # Try root as fallback
        resp = requests.get(config.SOULSYNC_URL, timeout=TIMEOUT)
        if resp.status_code in (200, 404):
            return {"status": "ok", "url": config.SOULSYNC_URL}
        return {"status": "error", "message": f"SoulSync returned {resp.status_code}"}
    except requests.ConnectionError:
        return {"status": "error", "message": f"Could not connect to {config.SOULSYNC_URL}"}
    except requests.Timeout:
        return {"status": "error", "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}
