"""
plugin_tidarr.py — Rythmx downloader plugin for Tidarr.

Submits albums to a running Tidarr instance (https://github.com/cstaelen/tidarr)
which downloads music via the Tidal streaming service.

Resolution strategy (two-step):
  1. Search Tidarr's Newznab/Lidarr endpoint with artist + album name.
     GET /api/lidarr?t=search&q={artist} {album}&apikey={key}
  2. Parse the returned Newznab XML — extract the numeric Tidal album ID
     from the first <link> element (/api/lidarr/download/{id}/...) with
     a <guid> numeric fallback.
  3. Submit via GET /api/sabnzbd/api?mode=addurl&name={tidal_url} — returns
     a real nzo_id (e.g. "tidarr-34277251-1234567890") used for Step 4b polling.

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
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Matches the Tidal album ID from Tidarr's Newznab <link> URLs.
# Actual format (port 3030): http://host:3030/album/20115556       → group(1) = "20115556"
# Legacy format (port 8484): http://host:8484/api/lidarr/download/20115556/high → group(2)
_DOWNLOAD_URL_RE = re.compile(r"/album/(\d+)|/api/lidarr/download/(\d+)/")


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
        self._session = requests.Session()
        if self._key:
            self._session.headers.update({"X-Api-Key": self._key})

    # ------------------------------------------------------------------
    # DownloaderPlugin protocol surface
    # ------------------------------------------------------------------

    def submit(self, artist: str, album: str, metadata: dict) -> str:
        """
        Resolve the Tidal album ID then queue the download in Tidarr.

        Uses /api/sabnzbd/api?mode=addurl — Tidarr's SABnzbd-compatibility layer.
        This returns a real nzo_id (e.g. "tidarr-34277251-1234567890") that can
        be polled via mode=queue and mode=history for completion detection (Step 4b).

        Returns:
            The nzo_id string from Tidarr  — successfully queued; use for polling
            "unresolved:{artist}:{album}"  — Tidal ID could not be resolved
                                             or addurl returned no nzo_id
        """
        if not self._url or not self._key:
            logger.warning(
                "tidarr: TIDARR_URL or TIDARR_API_KEY not configured — skipping %s — %s",
                artist,
                album,
            )
            return f"unresolved:{artist}:{album}"

        tidal_id = self._resolve_tidal_id(artist, album)
        if tidal_id is None:
            logger.warning(
                "tidarr: could not resolve Tidal ID for %s — %s", artist, album
            )
            return f"unresolved:{artist}:{album}"

        tidal_url = f"https://listen.tidal.com/album/{tidal_id}"
        try:
            resp = self._session.get(
                f"{self._url}/api/sabnzbd/api",
                params={
                    "mode": "addurl",
                    "name": tidal_url,
                    "apikey": self._key,
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning(
                "tidarr: addurl failed for %s — %s: %s", artist, album, exc
            )
            return f"unresolved:{artist}:{album}"

        if resp.status_code != 200:
            logger.warning(
                "tidarr: addurl returned HTTP %d for %s — %s",
                resp.status_code, artist, album,
            )
            return f"unresolved:{artist}:{album}"

        try:
            data = resp.json()
        except Exception:
            logger.warning("tidarr: addurl response not JSON for %s — %s", artist, album)
            return f"unresolved:{artist}:{album}"

        nzo_ids = data.get("nzo_ids") or []
        if not nzo_ids:
            logger.warning(
                "tidarr: addurl returned no nzo_ids for %s — %s (response: %s)",
                artist, album, data,
            )
            return f"unresolved:{artist}:{album}"

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

    def _resolve_tidal_id(self, artist: str, album: str) -> Optional[str]:
        """
        Query Tidarr's Newznab/Lidarr indexer and extract the Tidal album ID.

        Primary search: t=search&q={artist} {album}
          This is the general Newznab search query and is the proven path
          (used by SoulSync and other *arr integrations against Tidarr).

        Fallback search: t=music&artist={artist}&album={album}
          The structured Newznab music search. More precise but not always
          available or differently ranked.

        The `apikey` query param is used because the Newznab spec requires
        it as a query parameter; the session X-Api-Key header is also sent.

        Returns the numeric Tidal album ID string, or None on failure.
        """
        # Primary: general search query (proven with Tidarr in the wild)
        result = self._newznab_search(
            params={"t": "search", "q": f"{artist} {album}", "apikey": self._key},
            artist=artist,
            album=album,
        )
        if result:
            return result

        # Fallback: structured music search
        return self._newznab_search(
            params={"t": "music", "artist": artist, "album": album, "apikey": self._key},
            artist=artist,
            album=album,
        )

    def _newznab_search(
        self, params: dict, artist: str, album: str
    ) -> Optional[str]:
        """Execute one Newznab search request and return the parsed Tidal ID, or None."""
        try:
            resp = self._session.get(
                f"{self._url}/api/lidarr",
                params=params,
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning(
                "tidarr: Newznab search request failed for %s — %s: %s",
                artist,
                album,
                exc,
            )
            return None

        if resp.status_code != 200:
            logger.warning(
                "tidarr: Newznab search returned HTTP %d for %s — %s",
                resp.status_code,
                artist,
                album,
            )
            return None

        return self._parse_newznab_id(resp.text, artist, album)

    def _parse_newznab_id(
        self, xml_text: str, artist: str, album: str
    ) -> Optional[str]:
        """
        Parse Tidarr's Newznab XML response and return the first Tidal album ID.

        Primary path (port 3030):
            <link>http://host:3030/album/20115556</link>
            → group(1) = "20115556"

        Legacy path (port 8484):
            <link>http://host:8484/api/lidarr/download/20115556/high</link>
            → group(2) = "20115556"

        Returns the numeric ID string, or None if no match found.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning(
                "tidarr: failed to parse Newznab XML for %s — %s: %s",
                artist,
                album,
                exc,
            )
            return None

        items = root.findall(".//item")
        if not items:
            logger.debug("tidarr: no Newznab results for %s — %s", artist, album)
            return None

        first = items[0]

        # Extract Tidal album ID from <link>.
        # Actual format:  http://host:3030/album/20115556        → group(1)
        # Legacy format:  http://host:8484/api/lidarr/download/20115556/high → group(2)
        link_el = first.find("link")
        if link_el is not None and link_el.text:
            m = _DOWNLOAD_URL_RE.search(link_el.text)
            if m:
                return m.group(1) or m.group(2)

        logger.warning(
            "tidarr: could not extract Tidal ID from Newznab result for %s — %s",
            artist,
            album,
        )
        return None

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
# Plugin registration — must appear after class definition
# ---------------------------------------------------------------------------

PLUGIN_API_VERSION = 1
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Downloads albums via Tidarr (Tidal) using SABnzbd pipeline."
PLUGIN = {"slot": "downloader", "class": TidarrDownloader}

CONFIG_SCHEMA = [
    {
        "key": "TIDARR_URL",
        "label": "Tidarr URL",
        "type": "url",
        "required": True,
        "placeholder": "http://tidarr:8484",
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
]
