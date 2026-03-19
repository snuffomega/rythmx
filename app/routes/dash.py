import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.clients import last_fm_client, plex_push, soulsync_api
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/discovery/candidates")
def discovery_candidates():
    from app.db import get_library_reader
    from app.services import engine
    sr = get_library_reader()

    candidates = sr.get_discovery_pool(limit=200)
    similar_map = sr.get_similar_artists_map()
    top_artists = last_fm_client.get_top_artists()
    loved = last_fm_client.get_loved_artist_names()

    candidates = engine.apply_owned_check(candidates, sr)
    scored = engine.score_candidates(candidates, similar_map, top_artists, loved)
    return {"status": "ok", "candidates": scored}


@router.get("/discovery/playlist")
def discovery_playlist():
    tracks = rythmx_store.get_playlist()
    return {"status": "ok", "playlist": tracks}


@router.post("/discovery/playlist")
def discovery_playlist_add(
    data: Optional[dict[str, Any]] = Body(default=None),
):
    data = data or {}
    if not data.get("track_id") and not data.get("spotify_track_id"):
        return JSONResponse(
            {"status": "error", "message": "track_id or spotify_track_id required"},
            status_code=400,
        )
    rythmx_store.add_to_playlist(data)
    return {"status": "ok"}


@router.delete("/discovery/playlist/{track_id:path}")
def discovery_playlist_remove(track_id: str):
    rythmx_store.remove_from_playlist(track_id)
    return {"status": "ok"}


@router.post("/discovery/download")
def discovery_download(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    track = {
        "track_name": data.get("track_name"),
        "artist_name": data.get("artist_name"),
        "album_name": data.get("album_name"),
        "spotify_track_id": data.get("spotify_track_id"),
    }
    if not track["track_name"] or not track["artist_name"]:
        return JSONResponse(
            {"status": "error", "message": "track_name and artist_name required"},
            status_code=400,
        )
    result = soulsync_api.queue_download(track)
    return result


@router.post("/discovery/publish")
def discovery_publish():
    tracks = rythmx_store.get_playlist()
    rating_keys = [t["track_id"] for t in tracks if t.get("track_id")]
    if not rating_keys:
        return JSONResponse(
            {"status": "error", "message": "No owned tracks in playlist to push"},
            status_code=400,
        )
    rythmx_store.create_playlist_meta("For You", source="new_music", mode="library_only")
    playlist_id = plex_push.create_or_update_playlist("For You", rating_keys)
    if playlist_id:
        rythmx_store.update_playlist_plex_id("For You", playlist_id)
        return {"status": "ok", "plex_playlist_id": playlist_id}
    return JSONResponse(
        {"status": "error", "message": "Plex push failed — check logs"}, status_code=500
    )


@router.post("/discovery/export")
def discovery_export():
    tracks = rythmx_store.get_playlist()
    if not tracks:
        return JSONResponse(
            {"status": "error", "message": "Playlist is empty"}, status_code=400
        )
    lines = ["#EXTM3U"]
    for t in tracks:
        lines.append(f"#EXTINF:-1,{t.get('artist_name', '')} - {t.get('track_name', '')}")
        if t.get("spotify_track_id"):
            lines.append(f"# spotify:{t['spotify_track_id']}")
    return {"status": "ok", "content": "\n".join(lines), "filename": "for-you.m3u"}
