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
from typing import Any, Protocol, TypedDict, runtime_checkable

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


class ConfigField(TypedDict, total=False):
    """One entry in a plugin's CONFIG_SCHEMA list — describes a config field for the UI."""
    key: str           # env-style key, e.g. TIDARR_URL (matches os.environ key plugin reads)
    label: str         # human-readable label
    type: str          # "text" | "url" | "password" | "select"
    required: bool
    default: str
    options: list[str] # for type="select"
    placeholder: str


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

# Catalog — populated by load_plugins(); used by the settings API to build the
# plugin management UI. Key = plugin name (e.g. "tidarr").
_plugin_catalog: dict[str, dict[str, Any]] = {}


def get_downloader() -> DownloaderPlugin:
    return _registry["downloader"]  # type: ignore[return-value]


def get_tagger() -> TaggerPlugin:
    return _registry["tagger"]  # type: ignore[return-value]


def get_file_handler() -> FileHandlerPlugin:
    return _registry["file_handler"]  # type: ignore[return-value]


def get_plugin_catalog() -> dict[str, dict[str, Any]]:
    """Returns a copy of the plugin catalog populated at last load_plugins() call."""
    return dict(_plugin_catalog)


def reload_plugins() -> None:
    """
    Re-initialize all slots to stubs, then re-scan and re-load from disk.
    Reads slot_config and plugin_settings fresh from the DB — call after saving
    plugin config changes via the settings API.
    """
    from app.db import rythmx_store as _store  # lazy — avoids circular import at module load

    _registry["downloader"] = _StubDownloader()
    _registry["tagger"] = _NoopTagger()
    _registry["file_handler"] = _NoopFileHandler()
    _plugin_catalog.clear()

    slot_config = _store.get_all_plugin_slot_config()
    plugin_settings = _store.get_all_plugin_settings()
    load_plugins(slot_config=slot_config, plugin_settings=plugin_settings)


# ---------------------------------------------------------------------------
# Loader — called once at app startup from main.py lifespan
# ---------------------------------------------------------------------------

def load_plugins(
    slot_config: dict[tuple[str, str], bool] | None = None,
    plugin_settings: dict[str, dict[str, str]] | None = None,
) -> None:
    """
    Scans plugins/ directory for Python files matching plugin_*.py.

    slot_config: {(plugin_name, slot): enabled} — from DB; slots set False are skipped.
    plugin_settings: {plugin_name: {config_key: value}} — from DB; patches os.environ
        before each plugin is instantiated so DB values override .env defaults.

    Plugin manifest (module-level):
      PLUGIN_API_VERSION = 1           (required — skipped if unsupported)
      PLUGIN_SLOTS = {"downloader": MyClass, "file_handler": MyMover}  (multi-slot)
      PLUGIN = {"slot": "downloader", "class": MyClass}                (single-slot, legacy)
      CONFIG_SCHEMA = [ConfigField, ...]   (optional — describes config fields for the UI)
      PLUGIN_VERSION = "1.0.0"             (optional — displayed in UI)
      PLUGIN_DESCRIPTION = "..."           (optional — displayed in UI)

    Plugins that fail to load are skipped with a warning — core always falls back to stubs.
    """
    if not PLUGINS_DIR.exists():
        return

    for path in sorted(PLUGINS_DIR.glob("plugin_*.py")):
        plugin_name = path.stem[len("plugin_"):]  # "plugin_tidarr" → "tidarr"
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            # Version gate
            declared_version = getattr(module, "PLUGIN_API_VERSION", None)
            if declared_version is not None and declared_version not in SUPPORTED_PLUGIN_API_VERSIONS:
                logger.warning(
                    "Plugin %s declares PLUGIN_API_VERSION=%s — supported: %s — skipped",
                    path.name, declared_version, sorted(SUPPORTED_PLUGIN_API_VERSIONS),
                )
                continue

            # Resolve slots map from PLUGIN_SLOTS (multi-slot) or PLUGIN (single-slot)
            plugin_slots_raw = getattr(module, "PLUGIN_SLOTS", None)
            if isinstance(plugin_slots_raw, dict):
                slots_map: dict[str, type] = {
                    k: v for k, v in plugin_slots_raw.items()
                    if isinstance(k, str) and v is not None
                }
            else:
                plugin_meta = getattr(module, "PLUGIN", None)
                if not isinstance(plugin_meta, dict):
                    logger.warning("Plugin %s: missing PLUGIN or PLUGIN_SLOTS dict — skipped", path.name)
                    continue
                slot = plugin_meta.get("slot")
                cls = plugin_meta.get("class")
                if not slot or cls is None:
                    logger.warning("Plugin %s: invalid PLUGIN dict — skipped", path.name)
                    continue
                slots_map = {slot: cls}

            # Apply DB config overrides to os.environ before instantiation
            if plugin_settings:
                for key, val in (plugin_settings.get(plugin_name) or {}).items():
                    os.environ[key] = val

            # Instantiate each slot, respecting DB enable/disable config
            active_slots: list[str] = []
            for slot_name, cls in slots_map.items():
                if slot_name not in _registry:
                    logger.warning("Plugin %s: unknown slot '%s' — skipped", path.name, slot_name)
                    continue

                if slot_config is not None:
                    if not slot_config.get((plugin_name, slot_name), True):
                        logger.info(
                            "Plugin %s slot '%s' disabled by config — skipping",
                            path.name, slot_name,
                        )
                        continue

                try:
                    instance = cls()
                    _registry[slot_name] = instance
                    active_slots.append(slot_name)
                    logger.info("Plugin loaded: %s → slot '%s'", path.name, slot_name)
                except Exception as init_exc:
                    logger.warning(
                        "Plugin %s slot '%s' init failed: %s — using stub",
                        path.name, slot_name, init_exc,
                    )

            # Register in catalog (even if no slots were activated — still shows in UI)
            _plugin_catalog[plugin_name] = {
                "name": plugin_name,
                "version": getattr(module, "PLUGIN_VERSION", None),
                "description": getattr(module, "PLUGIN_DESCRIPTION", None),
                "slots": list(slots_map.keys()),
                "active_slots": active_slots,
                "config_schema": list(getattr(module, "CONFIG_SCHEMA", None) or []),
                "module_path": str(path),
            }

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s — using stub", path.name, e)
