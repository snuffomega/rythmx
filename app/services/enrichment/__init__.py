"""
enrichment — Modular library enrichment package.

Re-exports all public names so callers can use:
    from app.services.enrichment import enrich_library, get_status, ...
    or via the backward-compatible facade:
    from app.services import library_service; library_service.enrich_library(...)
"""

# Stage 1 — Sync
from app.services.enrichment.sync import sync_library  # noqa: F401

# Stage 2 — ID resolution
from app.services.enrichment.id_itunes_deezer import enrich_library  # noqa: F401
from app.services.enrichment.id_spotify import (  # noqa: F401
    enrich_artist_ids_spotify,
    enrich_spotify,
    get_spotify_status,
)
from app.services.enrichment.id_lastfm import enrich_artist_ids_lastfm  # noqa: F401

# Stage 3 — Rich data workers
from app.services.enrichment.rich_itunes import enrich_itunes_rich  # noqa: F401
from app.services.enrichment.rich_deezer import enrich_deezer_release  # noqa: F401
from app.services.enrichment.rich_spotify import enrich_genres_spotify  # noqa: F401
from app.services.enrichment.tags_lastfm import (  # noqa: F401
    enrich_tags_lastfm,
    enrich_lastfm_tags,
    get_lastfm_tags_status,
)
from app.services.enrichment.stats_lastfm import enrich_stats_lastfm  # noqa: F401
from app.services.enrichment.bpm_deezer import (  # noqa: F401
    enrich_deezer_bpm,
    get_deezer_bpm_status,
)
from app.services.enrichment.art_artist import enrich_artist_art  # noqa: F401

# Status
from app.services.enrichment.status import get_status  # noqa: F401

# Pipeline
from app.services.enrichment.pipeline import (  # noqa: F401
    run_auto_pipeline,
    is_pipeline_running,
)
