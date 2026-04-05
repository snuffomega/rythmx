"""
status.py — General library enrichment status for the Settings UI.
"""
from app import config
from app.db import rythmx_store
from app.db.rythmx_store import _connect


def get_status() -> dict:
    """
    Return combined sync + enrich status for the Settings UI.
    Always safe to call — returns sane defaults if tables don't exist yet.
    """
    last_synced = rythmx_store.get_setting("library_last_synced")
    backend = rythmx_store.get_setting("library_platform") or config.LIBRARY_PLATFORM

    try:
        with _connect() as conn:
            track_row = conn.execute("SELECT COUNT(*) FROM lib_tracks WHERE removed_at IS NULL").fetchone()
            track_count = track_row[0] if track_row else 0

            album_row = conn.execute("SELECT COUNT(*) FROM lib_albums WHERE removed_at IS NULL").fetchone()
            total_albums = album_row[0] if album_row else 0

            enriched_row = conn.execute(
                "SELECT COUNT(*) FROM lib_albums WHERE removed_at IS NULL AND (itunes_album_id IS NOT NULL OR deezer_id IS NOT NULL)"
            ).fetchone()
            enriched_albums = enriched_row[0] if enriched_row else 0
    except Exception:
        track_count = 0
        total_albums = 0
        enriched_albums = 0

    enrich_pct = round(enriched_albums / total_albums * 100) if total_albums else 0

    return {
        "synced": track_count > 0,
        "last_synced": last_synced,
        "platform": platform,
        "track_count": track_count,
        "total_albums": total_albums,
        "enriched_albums": enriched_albums,
        "enrich_pct": enrich_pct,
    }
