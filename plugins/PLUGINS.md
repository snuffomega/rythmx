# Rythmx Plugins

Plugins extend Rythmx without touching the core app. A plugin that fails to load logs a warning and the core continues with the built-in stub — no crash, no instability.

---

## Plugin Slots

| Slot | What it does | Default |
|------|-------------|---------|
| `downloader` | Submits an album to a download service | Stub (logs, no-op) |
| `tagger` | Tags a downloaded file with metadata | No-op |
| `file_handler` | Organizes/moves a file after tagging | No-op (returns original path) |

---

## How to Write a Plugin

Create a file in this directory named `plugin_<name>.py`. Define a class implementing the relevant Protocol, then expose a `PLUGIN` dict:

```python
# plugins/plugin_lidarr.py

PLUGIN_API_VERSION = 1  # must match a supported version in app/plugins/__init__.py
PLUGIN = {"slot": "downloader", "class": LidarrDownloader}


class LidarrDownloader:
    name = "lidarr"

    def submit(self, artist: str, album: str, metadata: dict) -> str:
        """Submit album to Lidarr. Returns Lidarr job ID."""
        import requests
        # ... your implementation ...
        return job_id

    def test_connection(self) -> dict:
        """Returns {"status": "ok"} or {"status": "error", "message": "..."}"""
        try:
            # ... ping Lidarr API ...
            return {"status": "ok", "message": "Lidarr reachable"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
```

The plugin is auto-discovered on app startup via `app/plugins/__init__.py:load_plugins()`.

---

## Rules

- File must be named `plugin_<name>.py`
- Must declare `PLUGIN_API_VERSION = 1` at module level — load_plugins() skips plugins with unsupported versions
- Must define `PLUGIN = {"slot": "<slot_name>", "class": <YourClass>}`
- Class must implement the Protocol for its slot (see `app/plugins/__init__.py`)
- Never import from `app/db/` — plugins are not allowed to touch SQLite directly
- Never log secrets or raw credentials
- `test_connection()` must always return a dict with a `status` key

---

## Multi-Slot Plugins (`PLUGIN_SLOTS`)

A plugin can fill more than one slot by declaring `PLUGIN_SLOTS` instead of `PLUGIN`:

```python
PLUGIN_SLOTS = {
    "downloader": MyDownloader,
    "file_handler": MyMover,    # optional — only if the plugin handles moving too
}
```

Each slot must implement its Protocol independently. Users can enable/disable individual slots from Settings → Integrations without touching the others.

---

## Config Schema (`CONFIG_SCHEMA`)

Declare a `CONFIG_SCHEMA` list to expose config fields in the Settings → Integrations UI.
Values are saved to the DB and injected into `os.environ` before the plugin class is instantiated —
no manual `.env` editing required after initial setup.

```python
CONFIG_SCHEMA = [
    {"key": "MY_URL",      "label": "Service URL",  "type": "url",      "required": True},
    {"key": "MY_API_KEY",  "label": "API Key",       "type": "password", "required": True},
    {"key": "MY_QUALITY",  "label": "Quality",       "type": "select",   "required": False,
     "default": "lossless", "options": ["lossless", "hires_lossless"]},
]
```

| Field | Type | Description |
|---|---|---|
| `key` | `str` | Matches the `os.environ` key the plugin reads |
| `label` | `str` | Human-readable label for the UI |
| `type` | `str` | `"text"` \| `"url"` \| `"password"` \| `"select"` |
| `required` | `bool` | Marks field as required in the UI |
| `default` | `str` | Default value shown in the UI |
| `options` | `list[str]` | For `type="select"` — list of choices |
| `placeholder` | `str` | Placeholder text for text/url/password inputs |

Plugin config is stored in `app_settings` under the key pattern `plugin.{name}.{config_key}`.
Password fields are never returned in plain text from `GET /settings/plugins`.

---

## Optional Metadata Fields

Declare `PLUGIN_VERSION` and `PLUGIN_DESCRIPTION` to improve the UI card:

```python
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Downloads albums via MyService using SABnzbd pipeline."
```

---

## Metadata Dict Contract

The `metadata: dict` argument passed to `submit()`, `tag()`, and `organize()` follows the `PluginMetadata` shape defined in `app/plugins/__init__.py`. All fields are optional — always use `.get()`:

| Key | Type | Description |
|---|---|---|
| `track_id` | `int` | `lib_tracks.id` — single-track submissions only |
| `release_id` | `int` | `lib_releases.id` |
| `isrc` | `str` | ISRC code (ISO 3901) |
| `deezer_id` | `int` | Deezer album ID |
| `itunes_id` | `int` | iTunes collection ID |
| `musicbrainz_id` | `str` | MusicBrainz release MBID |
| `explicit` | `bool` | Explicit content flag |
| `thumb_url` | `str` | Best available artwork URL |
| `duration_ms` | `int` | Track duration in milliseconds |
| `release_date` | `str` | YYYY-MM-DD (primary resolved date) |
| `label` | `str` | Record label |
| `upc` | `str` | UPC barcode |

---

## Protocol Reference

### DownloaderPlugin

```python
class DownloaderPlugin(Protocol):
    name: str                                                   # Display name
    def submit(self, artist: str, album: str, metadata: dict) -> str: ...  # Returns job ID
    def test_connection(self) -> dict: ...                      # {"status": "ok"} or error
```

### TaggerPlugin

```python
class TaggerPlugin(Protocol):
    name: str
    def tag(self, file_path: str, metadata: dict) -> None: ...
```

### FileHandlerPlugin

```python
class FileHandlerPlugin(Protocol):
    name: str
    def organize(self, file_path: str, metadata: dict) -> str: ...  # Returns new path
```

---

## Testing a Plugin Without Wiring

Drop your file here. If `PLUGIN` is defined correctly, it loads on next app start. Check logs:

```
INFO app.plugins: Plugin loaded: plugin_lidarr.py → slot 'downloader'
```

If it fails:
```
WARNING app.plugins: Plugin plugin_lidarr.py failed to load: ... — using stub
```

Core keeps running either way.

---

## Wiring the Downloader Slot to the Acquisition Queue

When you're ready to connect the downloader to the actual queue worker, edit one function:

```python
# app/services/acquisition.py

from app.plugins import get_downloader

def _submit_item(artist_name: str, album_title: str, metadata: dict) -> str:
    downloader = get_downloader()
    return downloader.submit(artist_name, album_title, metadata)
```

That's the only core file that changes.
