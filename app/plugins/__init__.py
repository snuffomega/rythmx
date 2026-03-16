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
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parents[2] / "plugins"


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
