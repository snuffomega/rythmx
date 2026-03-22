import logging

from fastapi import APIRouter, Depends, Query

from app.db import rythmx_store
from app.clients import last_fm_client
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/stats/top-artists")
def stats_top_artists(period: str = "6month", limit: int = Query(default=50, le=200)):
    ranked = last_fm_client.get_top_artists_ranked(period=period, limit=limit)
    return {"status": "ok", "artists": ranked, "period": period}


@router.get("/stats/top-tracks")
def stats_top_tracks(period: str = "6month", limit: int = Query(default=50, le=200)):
    tracks = last_fm_client.get_top_tracks(period=period, limit=limit)
    return {"status": "ok", "tracks": tracks, "period": period}


@router.get("/stats/top-albums")
def stats_top_albums(period: str = "6month", limit: int = Query(default=50, le=200)):
    albums = last_fm_client.get_top_albums(period=period, limit=limit)
    return {"status": "ok", "albums": albums, "period": period}


@router.get("/stats/summary")
def stats_summary():
    summary = rythmx_store.get_history_summary()
    return {"status": "ok", "summary": summary}


@router.get("/stats/loved-artists")
def stats_loved_artists():
    loved = last_fm_client.get_loved_artist_names()
    artists = [{"name": name} for name in sorted(loved)]
    return {"status": "ok", "artists": artists}
