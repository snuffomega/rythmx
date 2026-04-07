"""
Rythmx Plugin System

Defines the Protocol interfaces for all plugin slots.
Plugins are loaded from the top-level plugins/ directory via load_plugins().

Rules:
- Core never imports from plugins/ directly — always through this registry
- A failed plugin load logs a warning and falls back to the stub — never crashes core
- Plugins must not import from app/db/ or write to any SQLite DB
"""
import importlib.util
import logging
import os
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parents[2] / "plugins"


# ---------------------------------------------------------------------------
# Plugin API versioning
# ---------------------------------------------------------------------------
# Plugins must declare PLUGIN_API_VERSION = 1 at module level.
# load_plugins() skips any plugin declaring an unsupported version.
# When the contract changes incompatibly, increment PLUGIN_API_VERSION here
# and document the migration in PLUGINS.md.
PLUGIN_API_VERSION = 1
SUPPORTED_PLUGIN_API_VERSIONS: frozenset[int] = frozenset({1})


class PluginMetadata(TypedDict, total=False):
    """
    Standardized metadata dict passed to all plugin slot methods.
    All fields are optional — plugins must use .get() and handle missing keys.
    artist and album are always passed as explicit positional args;
    this dict carries the additional enrichment context.
    """
    track_id: int          # lib_tracks.id (single-track submissions only)
    release_id: int        # lib_releases.id
    isrc: str              # ISRC code (ISO 3901)
    deezer_id: int         # Deezer album ID
    itunes_id: int         # iTunes collection ID
    musicbrainz_id: str    # MusicBrainz release MBID
    explicit: bool
    thumb_url: str         # Best available artwork URL
    duration_ms: int       # Track duration in milliseconds
    release_date: str      # YYYY-MM-DD (primary resolved date)
    label: str             # Record label
    upc: str               # UPC barcode


# ---------------------------------------------------------------------------
# Protocol definitions — the interface contract for each plugin slot
# ---------------------------------------------------------------------------

@runtime_checkable
class DownloaderPlugin(Protocol):
    """Submits an album for download. Returns a provider-specific job ID or 'stub'."""
    name: str

    def submit(self, artist: str, album: str, metadata: dict) -> str:
        ...

    def test_connection(self) -> dict:
        ...


@runtime_checkable
class TaggerPlugin(Protocol):
    """Tags a downloaded file with track/album metadata."""
    name: str

    def tag(self, file_path: str, metadata: dict) -> None:
        ...


@runtime_checkable
class FileHandlerPlugin(Protocol):
    """Organizes/moves a file after tagging. Returns the new path."""
    name: str

    def organize(self, file_path: str, metadata: dict) -> str:
        ...


# ---------------------------------------------------------------------------
# Built-in stubs — default behavior when no plugin is loaded
# ---------------------------------------------------------------------------

class _StubDownloader:
    name = "stub"

    def submit(self, artist: str, album: str, metadata: dict) -> str:
        logger.info("Downloader stub: would submit %s — %s", artist, album)
        return "stub"

    def test_connection(self) -> dict:
        return {"status": "ok", "message": "Stub downloader — no real connection"}


class _NoopTagger:
    name = "noop"

    def tag(self, file_path: str, metadata: dict) -> None:
        pass


class _NoopFileHandler:
    name = "noop"

    def organize(self, file_path: str, metadata: dict) -> str:
        return file_path


# ---------------------------------------------------------------------------
# Registry — populated by load_plugins()
# ---------------------------------------------------------------------------

_registry: dict[str, object] = {
    "downloader": _StubDownloader(),
    "tagger": _NoopTagger(),
    "file_handler": _NoopFileHandler(),
}


def get_downloader() -> DownloaderPlugin:
    return _registry["downloader"]  # type: ignore[return-value]


def get_tagger() -> TaggerPlugin:
    return _registry["tagger"]  # type: ignore[return-value]


def get_file_handler() -> FileHandlerPlugin:
    return _registry["file_handler"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Loader — called once at app startup from create_app()
# ---------------------------------------------------------------------------

def load_plugins() -> None:
    """
    Scans plugins/ directory for Python files matching plugin_ prefix.
    Each file can define a PLUGIN dict:
        PLUGIN = {"slot": "downloader", "class": MyDownloader}
    Failed loads are logged as warnings — core continues with stubs.
    """
    if not PLUGINS_DIR.exists():
        return

    for path in sorted(PLUGINS_DIR.glob("plugin_*.py")):
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            # Version gate — plugins declaring an unsupported API version are skipped.
            declared_version = getattr(module, "PLUGIN_API_VERSION", None)
            if declared_version is not None and declared_version not in SUPPORTED_PLUGIN_API_VERSIONS:
                logger.warning(
                    "Plugin %s declares PLUGIN_API_VERSION=%s — supported: %s — skipped",
                    path.name, declared_version, sorted(SUPPORTED_PLUGIN_API_VERSIONS),
                )
                continue

            plugin_meta = getattr(module, "PLUGIN", None)
            if not isinstance(plugin_meta, dict):
                logger.warning("Plugin %s: missing PLUGIN dict — skipped", path.name)
                continue

            slot = plugin_meta.get("slot")
            cls = plugin_meta.get("class")
            if slot not in _registry or cls is None:
                logger.warning("Plugin %s: invalid slot '%s' — skipped", path.name, slot)
                continue

            instance = cls()
            _registry[slot] = instance
            logger.info("Plugin loaded: %s → slot '%s'", path.name, slot)

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s — using stub", path.name, e)
