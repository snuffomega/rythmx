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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypedDict, runtime_checkable

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parents[2] / "plugins"


# ---------------------------------------------------------------------------
# Plugin API versioning
# ---------------------------------------------------------------------------
# Plugins must declare PLUGIN_API_VERSION = 2 at module level.
# load_plugins() skips any plugin declaring an unsupported version.
# When the contract changes incompatibly, increment PLUGIN_API_VERSION here
# and document the migration in docs/dev/plugin-contract.md.
PLUGIN_API_VERSION = 2
SUPPORTED_PLUGIN_API_VERSIONS: frozenset[int] = frozenset({2})

# Fetch plugin contract governance (downloader/tagger/file_handler)
FETCH_PLUGIN_CONTRACT_VERSION = 1
SUPPORTED_FETCH_PLUGIN_CONTRACT_VERSIONS: frozenset[int] = frozenset({1})
_FETCH_ERROR_TAXONOMY = frozenset({"recoverable", "permanent", "config"})

# Pre-fetch enrichment contract (optional downloader capability)
SUPPORTED_PRE_FETCH_ENRICHMENT_VERSIONS: frozenset[int] = frozenset({1})


class FetchPluginError(RuntimeError):
    """
    Standardized plugin error envelope for fetch pipeline plugins.

    The fetch worker maps this to task.error_type/error_code consistently.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "permanent",
        error_code: str = "plugin_error",
    ) -> None:
        super().__init__(message)
        self.error_type = (
            error_type if str(error_type).strip().lower() in _FETCH_ERROR_TAXONOMY else "permanent"
        )
        self.error_code = str(error_code or "plugin_error")


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


@dataclass
class DownloadArtifact:
    """
    Shared data envelope that flows through the post-download plugin pipeline.

    Created by Core (tidarr_poller) after completion detection:
      - job_id:     matches download_jobs.job_id
      - artist/album: from download_jobs row
      - source_dir: locally-accessible download directory (output of translate_path)
      - files:      absolute FLAC paths found in source_dir
      - metadata:   enrichment context (tagger may add to this)
      - dest_dir:   set by file_handler.organize() — library destination path

    Plugin authors receive this object and return it (optionally mutated).
    To use the type annotation in a plugin: from app.plugins import DownloadArtifact
    """
    job_id: str
    artist: str
    album: str
    source_dir: str
    files: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    dest_dir: str | None = None


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

    def translate_path(self, storage_path: str) -> str:
        """
        Translate a downloader-internal storage path to a locally accessible path.

        Default behaviour (identity) is correct for any downloader whose output
        directory is already accessible at the same path inside Rythmx.
        Override only when the downloader container uses a different internal path
        than the Rythmx-mounted path (e.g. Tidarr writes to /shared/nzb_downloads,
        Rythmx mounts the same host dir at /app/downloads).
        """
        return storage_path


@runtime_checkable
class TaggerPlugin(Protocol):
    """Tags downloaded files. Receives a DownloadArtifact, returns it (optionally mutated)."""
    name: str

    def tag(self, artifact: DownloadArtifact) -> DownloadArtifact:
        ...


@runtime_checkable
class FileHandlerPlugin(Protocol):
    """Organizes/moves downloaded files into the library. Returns the mutated artifact."""
    name: str

    def organize(self, artifact: DownloadArtifact) -> DownloadArtifact:
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

    def translate_path(self, storage_path: str) -> str:
        return storage_path


class _NoopTagger:
    name = "noop"

    def tag(self, artifact: DownloadArtifact) -> DownloadArtifact:
        return artifact


class _NoopFileHandler:
    name = "noop"

    def organize(self, artifact: DownloadArtifact) -> DownloadArtifact:
        return artifact


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

def _validate_fetch_manifest(module: Any, slots_map: dict[str, type]) -> tuple[bool, str]:
    fetch_slots = {slot for slot in slots_map.keys() if slot in {"downloader", "tagger", "file_handler"}}
    if not fetch_slots:
        return True, ""

    capabilities = getattr(module, "CAPABILITIES", None)
    if not isinstance(capabilities, dict):
        return False, "missing CAPABILITIES dict for fetch-capable plugin"

    contract_version = capabilities.get("fetch_contract_version", 0)
    try:
        contract_version_i = int(contract_version)
    except (TypeError, ValueError):
        contract_version_i = 0
    if contract_version_i not in SUPPORTED_FETCH_PLUGIN_CONTRACT_VERSIONS:
        return (
            False,
            f"unsupported fetch contract version '{contract_version}' "
            f"(supported: {sorted(SUPPORTED_FETCH_PLUGIN_CONTRACT_VERSIONS)})",
        )

    roles_raw = capabilities.get("roles", [])
    roles = {str(r).strip() for r in roles_raw} if isinstance(roles_raw, list) else set()
    if not fetch_slots.issubset(roles):
        return False, f"CAPABILITIES.roles must include slots {sorted(fetch_slots)}"

    taxonomy_raw = capabilities.get("error_taxonomy", [])
    taxonomy = (
        {str(v).strip().lower() for v in taxonomy_raw}
        if isinstance(taxonomy_raw, list)
        else set()
    )
    if not _FETCH_ERROR_TAXONOMY.issubset(taxonomy):
        return (
            False,
            f"CAPABILITIES.error_taxonomy must include {sorted(_FETCH_ERROR_TAXONOMY)}",
        )

    plugin_version = getattr(module, "PLUGIN_VERSION", None)
    plugin_description = getattr(module, "PLUGIN_DESCRIPTION", None)
    if not plugin_version or not plugin_description:
        return False, "PLUGIN_VERSION and PLUGIN_DESCRIPTION are required for fetch-capable plugins"

    # Validate pre_fetch_enrichment_version if declared (optional capability)
    pre_fetch_enrich_version = capabilities.get("pre_fetch_enrichment_version")
    if pre_fetch_enrich_version is not None:
        try:
            pre_fetch_enrich_version_i = int(pre_fetch_enrich_version)
        except (TypeError, ValueError):
            pre_fetch_enrich_version_i = 0
        if pre_fetch_enrich_version_i not in SUPPORTED_PRE_FETCH_ENRICHMENT_VERSIONS:
            return (
                False,
                f"unsupported pre_fetch_enrichment_version '{pre_fetch_enrich_version}' "
                f"(supported: {sorted(SUPPORTED_PRE_FETCH_ENRICHMENT_VERSIONS)})",
            )

    return True, ""


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

            manifest_ok, manifest_reason = _validate_fetch_manifest(module, slots_map)
            if not manifest_ok:
                logger.warning("Plugin %s: %s -- skipped", path.name, manifest_reason)
                continue

            # Apply DB config overrides to os.environ before instantiation
            if plugin_settings:
                for key, val in (plugin_settings.get(plugin_name) or {}).items():
                    # Empty DB values should fall back to compose/.env defaults
                    # rather than clobbering them with blank strings.
                    if val is None or str(val) == "":
                        continue
                    os.environ[key] = str(val)

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

                    # If pre_fetch_enrichment_version is declared, verify method exists
                    capabilities = getattr(module, "CAPABILITIES", {}) or {}
                    if slot_name == "downloader" and capabilities.get("pre_fetch_enrichment_version"):
                        if not hasattr(instance, "pre_fetch_enrich") or not callable(getattr(instance, "pre_fetch_enrich")):
                            raise ValueError(
                                f"Plugin declares pre_fetch_enrichment_version but missing pre_fetch_enrich() method"
                            )

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
                "capabilities": dict(getattr(module, "CAPABILITIES", None) or {}),
                "module_path": str(path),
            }

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s — using stub", path.name, e)
