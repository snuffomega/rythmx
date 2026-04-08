"""
plugin_tidarr.py — Rythmx downloader plugin for Tidarr.

Submits albums to a running Tidarr instance (https://github.com/cstaelen/tidarr)
which downloads music via the Tidal streaming service.

Resolution strategy (three-step):
  1. Search Tidarr's Newznab/Lidarr endpoint:
     GET /api/lidarr?t=search&q={artist} {album}&apikey={key}
  2. Score the results: filter to configured quality, fuzzy-match artist+album,
     pick the best result. Extract enclosure URL and Tidal album ID from <guid>
     (format: "{album_id}-{quality}").
  3. Download the NZB file bytes from the enclosure URL, then submit via:
     POST /api/sabnzbd/api?mode=addfile  (multipart/form-data)
     Returns a real nzo_id ("tidarr_nzo_{id}") used for Step 4b polling.
     addurl is NOT used — it returns tidarr_nzo_unknown and cannot be tracked.

Config — add to your .env:
  TIDARR_URL      Base URL of your Tidarr instance, e.g. http://tidarr:8484
  TIDARR_API_KEY  64-char API key. Retrieve via:
                    docker exec tidarr cat /shared/.tidarr-api-key
                  or via Tidarr Settings → Authentication → API Key.
  TIDARR_QUALITY  Download quality: lossless (default) or hires_lossless.

Constraints (enforced by the plugin system):
  - Never imports from app/db/ — no SQLite access permitted.
  - Never logs the raw TIDARR_API_KEY value.
  - submit() never raises — unresolvable items return "unresolved:…" and log
    a warning so the caller can record the miss without crashing.
"""
import difflib
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Newznab namespace used by Tidarr's indexer for extended attributes.
_NEWZNAB_NS = "http://www.newzbin.com/DTD/2003/nzb"
_NZB_NS = {"newznab": "http://www.newznab.com/DTD:2010:feeds:attributes@"}


class TidarrDownloader:
    """
    Downloader plugin slot implementation pointing at a Tidarr instance.

    Instantiated once by load_plugins() at app startup and cached in _registry.
    Config is read from the environment at instantiation time.
    """

    name = "tidarr"

    def __init__(self) -> None:
        self._url = (os.environ.get("TIDARR_URL") or "").rstrip("/")
        self._key = os.environ.get("TIDARR_API_KEY") or ""
        quality_raw = (os.environ.get("TIDARR_QUALITY") or "lossless").lower()
        self._quality = quality_raw if quality_raw in ("lossless", "hires_lossless") else "lossless"
        # Path translation: Tidarr-internal prefix → locally accessible prefix.
        # Only needed when Tidarr's container path differs from Rythmx's mount point.
        # If both containers mount the same host dir at the same container path, leave blank.
        self._tidarr_prefix = (
            os.environ.get("FILE_MOVER_TIDARR_PREFIX") or ""
        ).rstrip("/")
        self._local_prefix = (
            os.environ.get("FILE_MOVER_LOCAL_PREFIX") or ""
        ).rstrip("/")
        self._session = requests.Session()
        if self._key:
            self._session.headers.update({"X-Api-Key": self._key})

    # ------------------------------------------------------------------
    # DownloaderPlugin protocol surface
    # ------------------------------------------------------------------

    def submit(self, artist: str, album: str, metadata: dict) -> str:
        """
        Find the best Tidal match for artist+album, download its NZB, and submit
        via mode=addfile (multipart POST) to get a real trackable nzo_id.

        Flow:
          1. Search Newznab → score results by quality + fuzzy artist/album match
          2. GET the enclosure URL to download raw NZB bytes
          3. POST NZB bytes to /api/sabnzbd/api?mode=addfile — returns real nzo_id

        NOTE: addurl is NOT used — it always returns "tidarr_nzo_unknown" when
        passed a tidal.com URL, making polling impossible.

        Returns:
            nzo_id string ("tidarr_nzo_{id}")  — queued, trackable
            "unresolved:{artist}:{album}"        — search/submit failed
        """
        if not self._url or not self._key:
            logger.warning(
                "tidarr: TIDARR_URL or TIDARR_API_KEY not configured — skipping %s — %s",
                artist, album,
            )
            return f"unresolved:{artist}:{album}"

        result = self._find_best_result(artist, album)
        if result is None:
            logger.warning("tidarr: no matching result for %s — %s", artist, album)
            return f"unresolved:{artist}:{album}"

        enclosure_url, tidal_id = result

        # Step 2: download the NZB bytes
        try:
            nzb_resp = self._session.get(enclosure_url, timeout=15)
            nzb_resp.raise_for_status()
            nzb_bytes = nzb_resp.content
        except requests.RequestException as exc:
            logger.warning(
                "tidarr: NZB download failed for %s — %s: %s", artist, album, exc
            )
            return f"unresolved:{artist}:{album}"

        # Step 3: submit via addfile (multipart POST — the only method that
        # returns a real nzo_id rather than tidarr_nzo_unknown)
        try:
            resp = self._session.post(
                f"{self._url}/api/sabnzbd/api",
                params={"mode": "addfile", "cat": "music", "apikey": self._key},
                files={"name": ("album.nzb", nzb_bytes, "application/x-nzb")},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning(
                "tidarr: addfile request failed for %s — %s: %s", artist, album, exc
            )
            return f"unresolved:{artist}:{album}"
        except Exception:
            logger.warning("tidarr: addfile response not JSON for %s — %s", artist, album)
            return f"unresolved:{artist}:{album}"

        if not data.get("status"):
            logger.warning(
                "tidarr: addfile failed for %s — %s: %s",
                artist, album, data.get("error", data),
            )
            return f"unresolved:{artist}:{album}"

        nzo_ids = data.get("nzo_ids") or []
        if not nzo_ids:
            logger.warning(
                "tidarr: addfile returned no nzo_ids for %s — %s", artist, album
            )
         ranslate_path(self, storage_path: str) -> str:
        """
        Translate a Tidarr-internal storage path to the Rythmx-accessible path.

        Tidarr reports storage paths using its own container root (e.g.
        /shared/nzb_downloads/tidarr_nzo_123/Artist/Album). If Rythmx mounts
        the same host directory at a different container path (e.g. /app/downloads),
        this method swaps the prefix so Core can locate the files.

        When FILE_MOVER_TIDARR_PREFIX and FILE_MOVER_LOCAL_PREFIX are both set:
          /shared/nzb_downloads/... → /app/downloads/...

        When not configured (default): returns path unchanged (identity).
        Volume tip: mount the same host dir at the same path in both containers
        and leave these settings empty — no translation needed.
        """
        if (
            self._tidarr_prefix
            and self._local_prefix
            and storage_path.startswith(self._tidarr_prefix)
        ):
            return self._local_prefix + storage_path[len(self._tidarr_prefix):]
        return storage_path

    def t   return f"unresolved:{artist}:{album}"

        nzo_id = nzo_ids[0]
        logger.info(
            "tidarr: queued %s — %s (tidal_id=%s nzo_id=%s quality=%s)",
            artist, album, tidal_id, nzo_id, self._quality,
        )
        return nzo_id

    def test_connection(self) -> dict:
        """
        Verify connectivity and auth against two Tidarr API surfaces:
          1. GET /api/settings         — main Tidarr API (validates auth)
          2. GET /api/sabnzbd/api?mode=version — Tidarr's built-in SABnzbd compatibility
             layer (NOT a separate service — Tidarr emulates SABnzbd protocol here
             for job tracking via addurl/queue/history).

        Used by the Settings → Integrations “Test connection” button.

        Returns:
            {"status": "ok", "message": "…"}    — reachable and authenticated
            {"status": "error", "message": "…"} — not reachable or bad key
        """
        if not self._url or not self._key:
            return {
                "status": "error",
                "message": "TIDARR_URL or TIDARR_API_KEY not configured in .env",
            }

        # Primary: settings endpoint (validates auth)
        try:
            resp = self._session.get(f"{self._url}/api/settings", timeout=8)
        except requests.exceptions.ConnectionError:
            return {
                "status": "error",
                "message": f"Cannot connect to Tidarr at {self._url}",
            }
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc)}

        if resp.status_code == 403:
            return {
                "status": "error",
                "message": "Invalid API key — check TIDARR_API_KEY (HTTP 403)",
            }
        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"Unexpected response from Tidarr: HTTP {resp.status_code}",
            }

        # Secondary: SABnzbd compatibility layer probe.
        # Tidarr emulates the SABnzbd protocol at /api/sabnzbd/api — NOT a separate
        # SABnzbd instance. Required for addurl/queue/history job tracking.
        try:
            sabnzbd_resp = self._session.get(
                f"{self._url}/api/sabnzbd/api",
                params={"mode": "version", "apikey": self._key},
                timeout=8,
            )
            sabnzbd_ok = sabnzbd_resp.status_code == 200
        except requests.RequestException:
            sabnzbd_ok = False

        if not sabnzbd_ok:
            return {
                "status": "ok",
                "message": (
                    f"Tidarr reachable at {self._url} — "
                    "warning: job tracking API (/api/sabnzbd/api) not responding"
                ),
            }

        return {"status": "ok", "message": f"Tidarr reachable at {self._url} — all OK"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_best_result(
        self, artist: str, album: str
    ) -> Optional[tuple[str, str]]:
        """
        Search Tidarr's Newznab indexer and return (enclosure_url, tidal_album_id)
        for the highest-confidence match at the configured quality.

        Strategy:
          1. Fetch Newznab XML via t=search&q={artist} {album}
          2. Filter items to those whose <guid> ends with the configured quality
             (format: "{album_id}-{quality}")
          3. Score remaining items using fuzzy match against <newznab:attr>
             artist and album values; fall back to title string matching
          4. Return the enclosure URL + album_id of the best-scoring item
        """
        xml_text = self._newznab_search_raw(artist, album)
        if xml_text is None:
            xml_text = self._newznab_search_raw(artist, album, structured=True)
        if xml_text is None:
            return None

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("tidarr: XML parse error for %s — %s: %s", artist, album, exc)
            return None

        items = root.findall(".//item")
        if not items:
            logger.debug("tidarr: no Newznab results for %s — %s", artist, album)
            return None

        # Filter to configured quality using <guid> = "{album_id}-{quality}"
        quality_items = [
            item for item in items
            if self._item_quality(item) == self._quality
        ]
        if not quality_items:
            logger.debug(
                "tidarr: no %s results for %s — %s, using any quality",
                self._quality, artist, album,
            )
            quality_items = items

        best_item = self._pick_best_item(quality_items, artist, album)
        if best_item is None:
            return None

        enclosure = best_item.find("enclosure")
        if enclosure is None:
            logger.warning("tidarr: best result has no enclosure for %s — %s", artist, album)
            return None
        enclosure_url = enclosure.get("url", "")
        if not enclosure_url:
            return None

        # guid format: "{album_id}-{quality}"  e.g. "33816889-lossless"
        guid_el = best_item.find("guid")
        guid_text = (guid_el.text or "") if guid_el is not None else ""
        tidal_id = guid_text.split("-")[0] if "-" in guid_text else guid_text
        if not tidal_id.isdigit():
            import re as _re
            m = _re.search(r"/download/([0-9]+)/", enclosure_url)
            tidal_id = m.group(1) if m else "unknown"

        logger.debug(
            "tidarr: selected tidal_id=%s quality=%s for %s — %s",
            tidal_id, self._quality, artist, album,
        )
        return enclosure_url, tidal_id

    def _newznab_search_raw(
        self, artist: str, album: str, structured: bool = False
    ) -> Optional[str]:
        """Execute one Newznab search and return raw XML text, or None on failure."""
        if structured:
            params: dict = {"t": "music", "artist": artist, "album": album, "apikey": self._key}
        else:
            params = {"t": "search", "q": f"{artist} {album}", "apikey": self._key}
        try:
            resp = self._session.get(
                f"{self._url}/api/lidarr",
                params=params,
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning(
                "tidarr: Newznab search failed for %s — %s: %s", artist, album, exc
            )
            return None
        if resp.status_code != 200:
            logger.warning(
                "tidarr: Newznab search HTTP %d for %s — %s", resp.status_code, artist, album
            )
            return None
        return resp.text

    @staticmethod
    def _item_quality(item: ET.Element) -> str:
        """Extract quality from <guid> text (format: '{album_id}-{quality}')."""
        guid_el = item.find("guid")
        if guid_el is None or not guid_el.text:
            return ""
        parts = guid_el.text.split("-", 1)
        return parts[1] if len(parts) == 2 else ""

    @staticmethod
    def _item_attrs(item: ET.Element) -> dict[str, str]:
        """
        Extract <newznab:attr name=... value=...> elements into a flat dict.
        Tidarr emits: artist, album, year, tracks, type, size.
        The namespace tag is matched by local name to avoid hardcoding the URI.
        """
        attrs: dict[str, str] = {}
        for el in item:
            tag = el.tag
            local = tag.split("}")[-1] if "}" in tag else tag
            if local == "attr":
                name = el.get("name", "")
                value = el.get("value", "")
                if name:
                    attrs[name] = value
        return attrs

    def _pick_best_item(
        self, items: list[ET.Element], artist: str, album: str
    ) -> Optional[ET.Element]:
        """
        Score each item by fuzzy matching artist+album against Newznab attrs,
        return the highest-scoring item.

        Score = average of artist_ratio and album_ratio (0.0–1.0).
        Items with score < 0.3 are discarded (likely garbage results).
        """
        artist_lower = artist.lower()
        album_lower = album.lower()
        best_score = -1.0
        best_item: Optional[ET.Element] = None

        for item in items:
            attrs = self._item_attrs(item)
            item_artist = (attrs.get("artist") or "").lower()
            item_album = (attrs.get("album") or "").lower()

            if item_artist and item_album:
                artist_score = difflib.SequenceMatcher(
                    None, artist_lower, item_artist
                ).ratio()
                album_score = difflib.SequenceMatcher(
                    None, album_lower, item_album
                ).ratio()
                score = (artist_score + album_score) / 2
            else:
                # No attrs — fall back to fuzzy match against <title>
                title_el = item.find("title")
                title = (title_el.text or "").lower() if title_el is not None else ""
                combined = f"{artist_lower} {album_lower}"
                score = difflib.SequenceMatcher(None, combined, title).ratio()

            if score > best_score:
                best_score = score
                best_item = item

        if best_score < 0.3:
            logger.warning(
                "tidarr: best match score %.2f too low for %s — %s (no confident result)",
                best_score, artist, album,
            )
            return None

        logger.debug("tidarr: best match score=%.2f for %s — %s", best_score, artist, album)
        return best_item

    # ------------------------------------------------------------------
    # Step 4b: polling helpers called by app/services/tidarr_poller.py
    # ------------------------------------------------------------------

    def poll_history(self, limit: int = 200) -> list[dict]:
        """
        Fetch completed/failed job slots from Tidarr's SABnzbd history endpoint.

        GET /api/sabnzbd/api?mode=history&limit={n}

        Returns a list of slot dicts with keys:
            nzo_id   — matches the ID stored in download_jobs.job_id
            name     — "Artist - Album" label
            status   — "Completed" | "Failed"
            storage  — absolute path on Tidarr host (only present on Completed)

        Returns [] on any network or parse error — callers should treat empty
        as "nothing resolved yet".
        """
        if not self._url or not self._key:
            return []
        try:
            resp = self._session.get(
                f"{self._url}/api/sabnzbd/api",
                params={"mode": "history", "limit": limit, "apikey": self._key},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("history", {}).get("slots", [])
        except Exception as exc:
            logger.warning("tidarr: poll_history error: %s", exc)
            return []

    def poll_queue(self) -> list[dict]:
        """
        Fetch in-progress job slots from Tidarr's SABnzbd queue endpoint.

        GET /api/sabnzbd/api?mode=queue

        Returns a list of slot dicts with keys:
            nzo_id    — matches download_jobs.job_id
            filename  — display name
            status    — "Downloading" | "Queued" | "Paused"

        Primarily used for UI progress display. Returns [] on error.
        """
        if not self._url or not self._key:
            return []
        try:
            resp = self._session.get(
                f"{self._url}/api/sabnzbd/api",
                params={"mode": "queue", "apikey": self._key},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("queue", {}).get("slots", [])
        except Exception as exc:
            logger.warning("tidarr: poll_queue error: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Plugin registration — must appear after class definitions
# ---------------------------------------------------------------------------

PLUGIN_API_VERSION = 2
PLUGIN_VERSION = "2.0.0"
PLUGIN_DESCRIPTION = "Downloads albums via Tidarr (Tidal) and copies FLACs to your library."
PLUGIN_SLOTS = {
    "downloader": TidarrDownloader,
}

CONFIG_SCHEMA = [
    {
        "key": "TIDARR_URL",
        "label": "Tidarr URL",
        "type": "url",
        "required": True,
        "placeholder": "http://tidarr:3030",
    },
    {
        "key": "TIDARR_API_KEY",
        "label": "API Key",
        "type": "password",
        "required": True,
        "placeholder": "64-character key from Tidarr Settings → Authentication",
    },
    {
        "key": "TIDARR_QUALITY",
        "label": "Quality",
        "type": "select",
        "required": False,
        "default": "lossless",
        "options": ["lossless", "hires_lossless"],
    },
    # --- Path translation (used by TidarrDownloader.translate_path) ---
    # Only needed when Tidarr's container path differs from Rythmx's mount point.
    # Leave blank if both containers mount the shared download dir at the same path.
    {
        "key": "FILE_MOVER_TIDARR_PREFIX",
        "label": "Tidarr download path (container-internal prefix)",
        "type": "text",
        "required": False,
        "placeholder": "/shared/nzb_downloads",
    },
    {
        "key": "FILE_MOVER_LOCAL_PREFIX",
        "label": "Download path (Rythmx mount point — same host dir)",
        "type": "text",
        "required": False,
        "placeholder": "/app/downloads",
    },
]
