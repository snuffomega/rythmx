"""
plugin_tidarr.py - Rythmx downloader plugin for Tidarr.

This plugin submits album downloads to Tidarr and polls completion via
Tidarr's SABnzbd-compatible API.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

import requests

from app.services.fetch_matching import evaluate_tidarr_candidates

logger = logging.getLogger(__name__)


def _env_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("tidarr: invalid %s='%s' - using default %.2f", name, raw, default)
        return default
    return max(minimum, min(value, maximum))


class TidarrDownloader:
    """
    Downloader plugin slot implementation pointing at a Tidarr instance.
    """

    name = "tidarr"

    def __init__(self) -> None:
        self._url = (os.environ.get("TIDARR_URL") or "").rstrip("/")
        self._key = os.environ.get("TIDARR_API_KEY") or ""
        quality_raw = (os.environ.get("TIDARR_QUALITY") or "lossless").lower()
        self._quality = quality_raw if quality_raw in ("lossless", "hires_lossless") else "lossless"
        self._match_min_score = _env_float(
            "TIDARR_MATCH_MIN_SCORE",
            default=0.86,
            minimum=0.50,
            maximum=0.99,
        )
        self._artist_min_overlap = _env_float(
            "TIDARR_ARTIST_MIN_OVERLAP",
            default=0.60,
            minimum=0.0,
            maximum=1.0,
        )
        self._album_min_overlap = _env_float(
            "TIDARR_ALBUM_MIN_OVERLAP",
            default=0.50,
            minimum=0.0,
            maximum=1.0,
        )
        # Path translation: Tidarr-internal prefix -> locally accessible prefix.
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
        Backward-compatible submit surface.
        """
        result = self.submit_with_match(artist, album, metadata or {})
        job_id = str(result.get("job_id") or "")
        if job_id:
            return job_id
        return f"unresolved:{artist}:{album}"

    def submit_with_match(self, artist: str, album: str, metadata: dict) -> dict[str, Any]:
        """
        Submit with structured match diagnostics for fetch task persistence.
        """
        if not self._url or not self._key:
            message = "TIDARR_URL or TIDARR_API_KEY not configured"
            logger.warning("tidarr: %s - skipping %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": "unresolved",
                "match_strategy": "search_score",
                "match_confidence": 0.0,
                "match_reasons": [message],
                "candidates": [],
                "error_message": message,
                "job_id": "",
            }

        metadata = dict(metadata or {})
        manual_tidal_id = str(metadata.get("manual_tidal_album_id") or "").strip()
        if manual_tidal_id.isdigit():
            preview = {
                "status": "confident",
                "match_status": "confident",
                "match_strategy": "manual_id",
                "match_confidence": 1.0,
                "match_reasons": ["manual_override"],
                "candidates": [
                    {
                        "tidal_id": manual_tidal_id,
                        "artist": artist,
                        "album": album,
                        "quality": self._quality,
                        "source": "manual_id",
                        "score": 1.0,
                    }
                ],
                "selected": {
                    "artist": artist,
                    "album": album,
                    "tidal_id": manual_tidal_id,
                    "quality": self._quality,
                    "source": "manual_id",
                    "enclosure_url": (
                        f"{self._url}/api/lidarr/download/"
                        f"{manual_tidal_id}/{self._quality}?apikey={self._key}"
                    ),
                },
            }
        else:
            preview = self.preview_match(artist, album, metadata)

        match_status = str(preview.get("match_status") or "unresolved")
        selected = preview.get("selected")
        enclosure_url = str((selected or {}).get("enclosure_url") or "").strip()
        tidal_id = str((selected or {}).get("tidal_id") or "").strip()
        if match_status != "confident" or not selected or not enclosure_url or not tidal_id:
            logger.warning("tidarr: no confident match for %s - %s", artist, album)
            return {
                "status": "unresolved",
                "match_status": match_status,
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []),
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": "No confident Tidarr match",
                "job_id": "",
            }

        try:
            nzb_resp = self._session.get(enclosure_url, timeout=15)
            nzb_resp.raise_for_status()
            nzb_bytes = nzb_resp.content
        except requests.RequestException as exc:
            message = f"NZB download failed: {exc}"
            logger.warning("tidarr: %s for %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": str(preview.get("match_status") or "unresolved"),
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []) + [message],
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": message,
                "job_id": "",
            }

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
            message = f"addfile request failed: {exc}"
            logger.warning("tidarr: %s for %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": str(preview.get("match_status") or "unresolved"),
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []) + [message],
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": message,
                "job_id": "",
            }
        except Exception as exc:
            message = f"addfile response not JSON: {exc}"
            logger.warning("tidarr: %s for %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": str(preview.get("match_status") or "unresolved"),
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []) + [message],
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": message,
                "job_id": "",
            }

        if not data.get("status"):
            message = f"addfile failed: {data.get('error', data)}"
            logger.warning("tidarr: %s for %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": str(preview.get("match_status") or "unresolved"),
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []) + [message],
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": message,
                "job_id": "",
            }

        nzo_ids = data.get("nzo_ids") or []
        if not nzo_ids:
            message = "addfile returned no nzo_ids"
            logger.warning("tidarr: %s for %s - %s", message, artist, album)
            return {
                "status": "unresolved",
                "match_status": str(preview.get("match_status") or "unresolved"),
                "match_strategy": str(preview.get("match_strategy") or "search_score"),
                "match_confidence": float(preview.get("match_confidence") or 0.0),
                "match_reasons": list(preview.get("match_reasons") or []) + [message],
                "candidates": list(preview.get("candidates") or []),
                "selected": selected,
                "error_message": message,
                "job_id": "",
            }

        nzo_id = str(nzo_ids[0])
        logger.info(
            "tidarr: queued %s - %s (tidal_id=%s nzo_id=%s quality=%s)",
            artist,
            album,
            tidal_id,
            nzo_id,
            self._quality,
        )
        return {
            "status": "submitted",
            "job_id": nzo_id,
            "match_status": str(preview.get("match_status") or "confident"),
            "match_strategy": str(preview.get("match_strategy") or "search_score"),
            "match_confidence": float(preview.get("match_confidence") or 0.0),
            "match_reasons": list(preview.get("match_reasons") or []),
            "candidates": list(preview.get("candidates") or []),
            "selected": selected,
        }

    def preview_match(self, artist: str, album: str, metadata: dict) -> dict[str, Any]:
        """
        Non-mutating match evaluation for dry-run proof tooling and fetch diagnostics.
        """
        metadata = dict(metadata or {})
        if not self._url or not self._key:
            return {
                "status": "unresolved",
                "match_status": "unresolved",
                "match_strategy": "search_score",
                "match_confidence": 0.0,
                "match_reasons": ["TIDARR_URL or TIDARR_API_KEY not configured"],
                "candidates": [],
                "selected": None,
            }

        expected_tidal_ids = self._expected_tidal_ids(metadata)
        searched = self._search_candidates(artist, album)
        if expected_tidal_ids:
            id_scoped = [
                candidate
                for candidate in searched
                if str(candidate.get("tidal_id") or "").strip() in expected_tidal_ids
            ]
            if not id_scoped:
                snapshot = [
                    {
                        "tidal_id": str(c.get("tidal_id") or ""),
                        "artist": str(c.get("artist") or ""),
                        "album": str(c.get("album") or ""),
                        "quality": str(c.get("quality") or ""),
                        "year": c.get("year"),
                        "track_count": c.get("track_count"),
                        "source": str(c.get("source") or "search"),
                        "score": float(c.get("score") or 0.0),
                    }
                    for c in searched[:10]
                ]
                return {
                    "status": "search_inconsistent",
                    "match_status": "search_inconsistent",
                    "match_strategy": "id_signature",
                    "match_confidence": 0.0,
                    "match_reasons": [
                        "expected_tidal_id_not_found_in_search",
                        f"expected_tidal_ids={','.join(sorted(expected_tidal_ids))}",
                    ],
                    "candidates": snapshot,
                    "selected": None,
                }
            evaluated = evaluate_tidarr_candidates(
                artist=artist,
                album=album,
                metadata=metadata,
                candidates=id_scoped,
                min_confidence=self._match_min_score,
                ambiguous_margin=0.04,
                snapshot_limit=10,
            )
        else:
            evaluated = evaluate_tidarr_candidates(
                artist=artist,
                album=album,
                metadata=metadata,
                candidates=searched,
                min_confidence=self._match_min_score,
                ambiguous_margin=0.04,
                snapshot_limit=10,
            )

        return {
            "status": str(evaluated.get("status") or "unresolved"),
            "match_status": str(evaluated.get("match_status") or "unresolved"),
            "match_strategy": str(evaluated.get("match_strategy") or "search_score"),
            "match_confidence": float(evaluated.get("match_confidence") or 0.0),
            "match_reasons": list(evaluated.get("match_reasons") or []),
            "candidates": list(evaluated.get("candidates") or []),
            "selected": evaluated.get("selected"),
        }

    @staticmethod
    def _expected_tidal_ids(metadata: dict[str, Any]) -> set[str]:
        expected: set[str] = set()
        for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
            raw = str((metadata or {}).get(key) or "").strip()
            if raw.isdigit():
                expected.add(raw)
        return expected

    def translate_path(self, storage_path: str) -> str:
        if (
            self._tidarr_prefix
            and self._local_prefix
            and storage_path.startswith(self._tidarr_prefix)
        ):
            return self._local_prefix + storage_path[len(self._tidarr_prefix):]
        return storage_path

    def test_connection(self) -> dict:
        if not self._url or not self._key:
            return {
                "status": "error",
                "message": "TIDARR_URL or TIDARR_API_KEY not configured in .env",
            }

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
                "message": "Invalid API key - check TIDARR_API_KEY (HTTP 403)",
            }
        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"Unexpected response from Tidarr: HTTP {resp.status_code}",
            }

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
                    f"Tidarr reachable at {self._url} - "
                    "warning: job tracking API (/api/sabnzbd/api) not responding"
                ),
            }

        return {"status": "ok", "message": f"Tidarr reachable at {self._url} - all OK"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_best_result(
        self,
        artist: str,
        album: str,
        metadata: dict,
    ) -> Optional[tuple[str, str]]:
        evaluated = self.preview_match(artist, album, metadata or {})
        selected = evaluated.get("selected") if isinstance(evaluated, dict) else None
        if not isinstance(selected, dict):
            return None
        enclosure = str(selected.get("enclosure_url") or "").strip()
        tidal_id = str(selected.get("tidal_id") or "").strip()
        if not enclosure or not tidal_id:
            return None
        return enclosure, tidal_id

    def _newznab_search_raw(
        self,
        artist: str,
        album: str,
        structured: bool = False,
    ) -> Optional[str]:
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
                "tidarr: Newznab search failed for %s - %s: %s",
                artist,
                album,
                exc,
            )
            return None
        if resp.status_code != 200:
            logger.warning(
                "tidarr: Newznab search HTTP %d for %s - %s",
                resp.status_code,
                artist,
                album,
            )
            return None
        return resp.text

    @staticmethod
    def _item_quality(item: ET.Element) -> str:
        guid_el = item.find("guid")
        if guid_el is None or not guid_el.text:
            return ""
        parts = guid_el.text.split("-", 1)
        return parts[1] if len(parts) == 2 else ""

    @staticmethod
    def _item_attrs(item: ET.Element) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for el in item:
            tag = el.tag
            local = tag.split("}")[-1] if "}" in tag else tag
            if local != "attr":
                continue
            name = str(el.get("name", "")).strip()
            value = str(el.get("value", "")).strip()
            if name:
                attrs[name] = value
        return attrs

    @staticmethod
    def _item_artist_album(item: ET.Element, attrs: dict[str, str]) -> tuple[str, str]:
        item_artist = str(attrs.get("artist") or "").strip()
        item_album = str(attrs.get("album") or "").strip()
        if item_artist and item_album:
            return item_artist, item_album

        title_el = item.find("title")
        title = str((title_el.text or "") if title_el is not None else "").strip()
        if " - " in title:
            left, right = title.split(" - ", 1)
            if not item_artist:
                item_artist = left.strip()
            if not item_album:
                item_album = right.strip()
        elif not item_album:
            item_album = title
        return item_artist, item_album

    def _item_to_candidate(self, item: ET.Element, *, source: str) -> dict[str, Any] | None:
        attrs = self._item_attrs(item)
        item_artist, item_album = self._item_artist_album(item, attrs)
        enclosure = item.find("enclosure")
        enclosure_url = str((enclosure.get("url", "") if enclosure is not None else "")).strip()
        if not item_artist or not item_album or not enclosure_url:
            return None

        guid_el = item.find("guid")
        guid_text = str((guid_el.text or "") if guid_el is not None else "").strip()
        tidal_id = guid_text.split("-", 1)[0] if "-" in guid_text else guid_text
        quality = guid_text.split("-", 1)[1] if "-" in guid_text else ""
        if not tidal_id.isdigit():
            match = re.search(r"/download/([0-9]+)/", enclosure_url)
            tidal_id = match.group(1) if match else ""
        if not quality:
            quality = self._item_quality(item)
        if not tidal_id:
            return None

        year = attrs.get("year")
        track_count = attrs.get("tracks") or attrs.get("track_count")
        return {
            "artist": item_artist,
            "album": item_album,
            "year": year,
            "track_count": track_count,
            "tidal_id": tidal_id,
            "quality": quality or self._quality,
            "enclosure_url": enclosure_url,
            "source": source,
            "__item": item,
        }

    def _search_candidates(self, artist: str, album: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for structured, source in ((True, "music"), (False, "search")):
            xml_text = self._newznab_search_raw(artist, album, structured=structured)
            if not xml_text:
                continue
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as exc:
                logger.warning("tidarr: XML parse error for %s - %s: %s", artist, album, exc)
                continue
            for item in root.findall(".//item"):
                candidate = self._item_to_candidate(item, source=source)
                if candidate:
                    candidates.append(candidate)

        # Deduplicate by release identity across both endpoints.
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (str(candidate.get("tidal_id") or ""), str(candidate.get("quality") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)

        preferred = [c for c in deduped if str(c.get("quality") or "") == self._quality]
        return preferred if preferred else deduped

    def _candidate_from_explicit_id(self, artist: str, album: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        raw = ""
        source = "id_signature"
        for key in ("manual_tidal_album_id", "tidal_album_id", "tidal_id"):
            value = str((metadata or {}).get(key) or "").strip()
            if value.isdigit():
                raw = value
                source = "manual_id" if key == "manual_tidal_album_id" else "id_signature"
                break
        if not raw:
            return None
        enclosure_url = f"{self._url}/api/lidarr/download/{raw}/{self._quality}?apikey={self._key}"
        return {
            "artist": artist,
            "album": album,
            "year": str((metadata or {}).get("release_date") or "")[:4],
            "track_count": (metadata or {}).get("track_count"),
            "tidal_id": raw,
            "quality": self._quality,
            "enclosure_url": enclosure_url,
            "source": source,
        }

    def _pick_best_item(
        self,
        items: list[ET.Element],
        artist: str,
        album: str,
        metadata: dict,
    ) -> Optional[ET.Element]:
        candidates: list[dict[str, Any]] = []
        for item in items:
            candidate = self._item_to_candidate(item, source="search")
            if candidate:
                candidates.append(candidate)
        evaluated = evaluate_tidarr_candidates(
            artist=artist,
            album=album,
            metadata=metadata or {},
            candidates=candidates,
            min_confidence=self._match_min_score,
            ambiguous_margin=0.04,
            snapshot_limit=5,
        )
        if str(evaluated.get("match_status") or "") != "confident":
            return None
        selected = evaluated.get("selected")
        if not isinstance(selected, dict):
            return None
        raw_item = selected.get("__item")
        return raw_item if isinstance(raw_item, ET.Element) else None

    # ------------------------------------------------------------------
    # Provider polling helpers
    # ------------------------------------------------------------------

    def poll_history(self, limit: int = 200) -> list[dict]:
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


PLUGIN_API_VERSION = 2
PLUGIN_VERSION = "2.2.0"
PLUGIN_DESCRIPTION = "Downloads albums via Tidarr (Tidal) with strict release matching."
CAPABILITIES = {
    "fetch_contract_version": 1,
    "roles": ["downloader"],
    "error_taxonomy": ["recoverable", "permanent", "config"],
}
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
        "placeholder": "64-character key from Tidarr Settings -> Authentication",
    },
    {
        "key": "TIDARR_QUALITY",
        "label": "Quality",
        "type": "select",
        "required": False,
        "default": "lossless",
        "options": ["lossless", "hires_lossless"],
    },
    {
        "key": "TIDARR_MATCH_MIN_SCORE",
        "label": "Minimum match score",
        "type": "text",
        "required": False,
        "default": "0.86",
        "placeholder": "0.86",
    },
    {
        "key": "TIDARR_ARTIST_MIN_OVERLAP",
        "label": "Minimum artist token overlap",
        "type": "text",
        "required": False,
        "default": "0.60",
        "placeholder": "0.60",
    },
    {
        "key": "TIDARR_ALBUM_MIN_OVERLAP",
        "label": "Minimum album token overlap",
        "type": "text",
        "required": False,
        "default": "0.50",
        "placeholder": "0.50",
    },
    {
        "key": "FILE_MOVER_TIDARR_PREFIX",
        "label": "Tidarr download path (container prefix)",
        "type": "text",
        "required": False,
        "placeholder": "/shared/nzb_downloads",
    },
    {
        "key": "FILE_MOVER_LOCAL_PREFIX",
        "label": "Download path (Rythmx mount point)",
        "type": "text",
        "required": False,
        "placeholder": "/app/downloads",
    },
]
