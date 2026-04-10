"""
plugin_tidarr.py - Rythmx downloader plugin for Tidarr.

This plugin submits album downloads to Tidarr and polls completion via
Tidarr's SABnzbd-compatible API.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any, Optional

import requests

from app.clients import musicbrainz_client
from app.db import rythmx_store
from app.services.enrichment._helpers import match_album_title, strip_title_suffixes
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

        # Check for enrichment-provided tidal_album_id (from pre_fetch_enrich)
        enriched_tidal_id = str(metadata.get("tidal_album_id") or "").strip()
        if enriched_tidal_id.isdigit():
            # Use enriched ID directly (from Tidal API resolution)
            preview = {
                "status": "confident",
                "match_status": "confident",
                "match_strategy": "enriched_id",
                "match_confidence": 0.86,  # Passed confidence threshold in enrichment
                "match_reasons": ["resolved_via_tidal_api"],
                "candidates": [
                    {
                        "tidal_id": enriched_tidal_id,
                        "artist": artist,
                        "album": album,
                        "quality": self._quality,
                        "source": "enriched_api",
                        "score": 0.86,
                    }
                ],
                "selected": {
                    "artist": artist,
                    "album": album,
                    "tidal_id": enriched_tidal_id,
                    "quality": self._quality,
                    "source": "enriched_api",
                    "enclosure_url": (
                        f"{self._url}/api/lidarr/download/"
                        f"{enriched_tidal_id}/{self._quality}?apikey={self._key}"
                    ),
                },
            }
        else:
            # Check for manual override
            manual_tidal_id = str(metadata.get("manual_tidal_album_id") or "").strip()
            if manual_tidal_id.isdigit():
                # Use manual ID (user selected via Activity UI)
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
                # STRICT GATE: No ID from enrichment or manual override → unresolved
                # Do NOT fall back to preview_match (text search)
                logger.warning(
                    "tidarr: no tidal_album_id from enrichment for %s - %s (requires manual selection)",
                    artist,
                    album,
                )
                return {
                    "status": "unresolved",
                    "match_status": "unresolved",
                    "match_strategy": "none",
                    "match_confidence": 0.0,
                    "match_reasons": [
                        "no_enriched_id_no_manual_override",
                        "user_intervention_required",
                    ],
                    "candidates": [],
                    "selected": None,
                    "error_message": "No tidal_album_id from enrichment. Manual selection required via Activity UI.",
                    "job_id": "",
                }

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
                fallback_eval = evaluate_tidarr_candidates(
                    artist=artist,
                    album=album,
                    metadata=metadata,
                    candidates=searched,
                    min_confidence=max(self._match_min_score, 0.92),
                    ambiguous_margin=0.06,
                    snapshot_limit=10,
                )
                selected = fallback_eval.get("selected")
                if isinstance(selected, dict) and self._is_strict_text_match(
                    artist,
                    album,
                    str(selected.get("artist") or ""),
                    str(selected.get("album") or ""),
                ):
                    reasons = list(fallback_eval.get("match_reasons") or [])
                    reasons.append("expected_tidal_id_missing_recovered_by_strict_title_match")
                    return {
                        "status": "confident",
                        "match_status": "confident",
                        "match_strategy": "search_score",
                        "match_confidence": float(fallback_eval.get("match_confidence") or 0.0),
                        "match_reasons": reasons,
                        "candidates": list(fallback_eval.get("candidates") or []),
                        "selected": selected,
                    }
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

    @staticmethod
    def _normalize_text(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower().strip()
        text = text.replace("&", " and ")
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _is_strict_text_match(
        self,
        expected_artist: str,
        expected_album: str,
        candidate_artist: str,
        candidate_album: str,
    ) -> bool:
        if self._normalize_text(expected_artist) != self._normalize_text(candidate_artist):
            return False
        expected_clean = self._normalize_text(strip_title_suffixes(expected_album))
        candidate_clean = self._normalize_text(strip_title_suffixes(candidate_album))
        if expected_clean == candidate_clean and expected_clean:
            return True
        return float(match_album_title(expected_album, candidate_album)) >= 0.98

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

    def pre_fetch_enrich(self, artist: str, album: str, metadata: dict) -> dict:
        """
        Resolve provider-specific Tidal IDs before fetch task submission.
        Phase 2: Cache-first artist resolution

        Tries A → B → {} chain:
        - A: Cached lookup (tidal_artist_id from lib_artists)
        - B: Fresh search (Tidal API → store in lib_artists → catalog match)
        - Fallback: manual override required
        """
        # A: Check cache first
        result = self._resolve_via_cached_artist(artist, album, metadata)
        if result:
            return result

        # B: Fresh search (with caching)
        result = self._resolve_via_fresh_artist_search(artist, album, metadata)
        if result:
            return result

        # Fallback: manual override required
        return {}

    def _resolve_via_cached_artist(self, artist: str, album: str, metadata: dict) -> dict | None:
        """
        Path A (cached): Look up tidal_artist_id from lib_artists.
        If found, use it to query artist catalog and match album.
        """
        try:
            # Normalize artist name for lookup
            normalized_artist = self._normalize_text(artist)

            with rythmx_store._connect() as conn:
                row = conn.execute(
                    "SELECT id, tidal_artist_id FROM lib_artists WHERE lower(replace(name, ' ', '')) = lower(?)",
                    (normalized_artist.replace(" ", ""),),
                ).fetchone()

            if not row or not row[1]:  # No cached tidal_artist_id
                return None

            artist_id = str(row[1])
            logger.debug("tidarr: found cached tidal_artist_id %s for '%s'", artist_id, artist)

            # Query catalog with cached artist ID
            token = self._get_tidal_token()
            if not token:
                return None

            # Query both ALBUMS and EPSANDSINGLES
            all_releases = []
            seen_ids = set()
            for filter_type in ("ALBUMS", "EPSANDSINGLES"):
                releases = self._get_artist_releases(token, artist_id, filter_type=filter_type)
                for r in releases:
                    album_id = r.get("id")
                    if album_id and album_id not in seen_ids:
                        all_releases.append(r)
                        seen_ids.add(album_id)

            if not all_releases:
                logger.debug("tidarr: no releases found for cached artist_id %s", artist_id)
                return None

            # Score albums
            candidates = [self._tidal_result_to_candidate(r) for r in all_releases]
            evaluated = evaluate_tidarr_candidates(
                artist=artist,
                album=album,
                metadata=metadata,
                candidates=candidates,
                min_confidence=0.86,
                ambiguous_margin=0.05,
                snapshot_limit=5,
            )

            if evaluated.get("match_status") == "confident":
                selected = evaluated.get("selected")
                if selected:
                    tidal_id = str(selected.get("tidal_id") or "").strip()
                    if tidal_id:
                        logger.info(
                            "tidarr: resolved tidal_id via cached artist: %s "
                            "(cached_artist_id=%s confidence=%.2f)",
                            tidal_id,
                            artist_id,
                            evaluated.get("match_confidence", 0.0),
                        )
                        return {"tidal_album_id": tidal_id}

            logger.debug(
                "tidarr: cached artist catalog found %d releases but no confident match "
                "(best_confidence=%.2f status=%s)",
                len(all_releases),
                evaluated.get("match_confidence", 0.0),
                evaluated.get("match_status"),
            )
        except Exception as e:
            logger.warning("tidarr: cached artist resolution failed: %s", e)

        return None

    def _resolve_via_fresh_artist_search(self, artist: str, album: str, metadata: dict) -> dict | None:
        """
        Path B (fresh): Search for artist on Tidal, store tidal_artist_id in DB,
        then query catalog and match album.
        """
        token = self._get_tidal_token()
        if not token:
            logger.debug("tidarr: tidal token not available for fresh artist search")
            return None

        try:
            # Step 1: Search and match artist
            artist_id = self._search_and_match_artist(token, artist)
            if not artist_id:
                logger.debug("tidarr: no confident artist match for '%s'", artist)
                return None

            logger.info("tidarr: resolved artist_id %s for '%s'", artist_id, artist)

            # Step 2: Store in DB (cache for future use)
            try:
                normalized_artist = self._normalize_text(artist)
                with rythmx_store._connect() as conn:
                    conn.execute(
                        """
                        UPDATE lib_artists
                        SET tidal_artist_id = ?
                        WHERE lower(replace(name, ' ', '')) = lower(?)
                        """,
                        (str(artist_id), normalized_artist.replace(" ", "")),
                    )
                logger.debug("tidarr: stored tidal_artist_id %s for '%s'", artist_id, artist)
            except Exception as e:
                logger.debug("tidarr: failed to store tidal_artist_id: %s", e)

            # Step 3: Get all releases by this artist (singles + EPs)
            all_releases = []
            seen_ids = set()
            for filter_type in ("ALBUMS", "EPSANDSINGLES"):
                releases = self._get_artist_releases(token, artist_id, filter_type=filter_type)
                for r in releases:
                    album_id = r.get("id")
                    if album_id and album_id not in seen_ids:
                        all_releases.append(r)
                        seen_ids.add(album_id)

            if not all_releases:
                logger.debug("tidarr: no releases found for artist_id %s", artist_id)
                return None

            # Step 4: Score using existing matcher
            candidates = [self._tidal_result_to_candidate(r) for r in all_releases]
            evaluated = evaluate_tidarr_candidates(
                artist=artist,
                album=album,
                metadata=metadata,
                candidates=candidates,
                min_confidence=0.86,
                ambiguous_margin=0.05,
                snapshot_limit=5,
            )

            if evaluated.get("match_status") == "confident":
                selected = evaluated.get("selected")
                if selected:
                    tidal_id = str(selected.get("tidal_id") or "").strip()
                    if tidal_id:
                        logger.info(
                            "tidarr: resolved tidal_id via fresh artist search: %s "
                            "(artist_id=%s confidence=%.2f stored_for_cache=true)",
                            tidal_id,
                            artist_id,
                            evaluated.get("match_confidence", 0.0),
                        )
                        return {"tidal_album_id": tidal_id}
            else:
                logger.debug(
                    "tidarr: fresh artist catalog search found %d releases but no confident match "
                    "(best_confidence=%.2f status=%s)",
                    len(all_releases),
                    evaluated.get("match_confidence", 0.0),
                    evaluated.get("match_status"),
                )
        except Exception as e:
            logger.warning("tidarr: fresh artist resolution failed: %s", e)

        return None

    def _resolve_via_musicbrainz(self, metadata: dict) -> dict | None:
        """
        If metadata has musicbrainz_release_id, fetch its URL relationships
        and look for tidal.com/album/{id} links.
        """
        mbid = str(metadata.get("musicbrainz_release_id") or "").strip()
        if not mbid:
            return None

        try:
            release = musicbrainz_client.get_release(mbid, inc="url-rels")
            if not release:
                return None

            # Parse URL rels for tidal.com/album/{id}
            url_rels = release.get("url-rels") or []
            for url_rel in url_rels:
                url = str(url_rel.get("url") or "")
                if "tidal.com/album/" in url:
                    match = re.search(r"/album/([0-9]+)", url)
                    if match:
                        tidal_id = match.group(1)
                        logger.info("tidarr: resolved tidal_id via MB url-rel: %s", tidal_id)
                        return {"tidal_album_id": tidal_id}
        except Exception as e:
            logger.debug("tidarr: _resolve_via_musicbrainz failed: %s", e)

        return None

    def _resolve_via_artist_catalog(self, artist: str, album: str, metadata: dict) -> dict | None:
        """
        Artist catalog search: resolve artist ID, query releases, match album.

        Flow:
        1. Search for artist by name
        2. Match artist with confidence scoring
        3. If confident, query artist's releases (EPSANDSINGLES filter)
        4. Match album from catalog using existing scorer
        5. Return album ID if confident match found
        """
        token = self._get_tidal_token()
        if not token:
            logger.debug("tidarr: tidal token not available for artist catalog search")
            return None

        try:
            # Step 1: Search for and match artist
            artist_id = self._search_and_match_artist(token, artist)
            if not artist_id:
                logger.debug("tidarr: no confident artist match for '%s'", artist)
                return None

            logger.info("tidarr: resolved artist_id %s for '%s'", artist_id, artist)

            # Step 2: Get all releases by this artist (albums + singles + EPs)
            # Query both ALBUMS and EPSANDSINGLES to get complete catalog, dedupe by ID
            seen_ids = set()
            all_releases = []
            for filter_type in ("ALBUMS", "EPSANDSINGLES"):
                releases = self._get_artist_releases(token, artist_id, filter_type=filter_type)
                for r in releases:
                    album_id = r.get("id")
                    if album_id and album_id not in seen_ids:
                        all_releases.append(r)
                        seen_ids.add(album_id)

            if not all_releases:
                logger.debug("tidarr: no releases found for artist_id %s", artist_id)
                return None

            releases = all_releases

            # Step 3: Convert to candidates and score using existing matcher
            candidates = [self._tidal_result_to_candidate(r) for r in releases]
            evaluated = evaluate_tidarr_candidates(
                artist=artist,
                album=album,
                metadata=metadata,
                candidates=candidates,
                min_confidence=0.86,
                ambiguous_margin=0.05,
                snapshot_limit=5,
            )

            if evaluated.get("match_status") == "confident":
                selected = evaluated.get("selected")
                if selected:
                    tidal_id = str(selected.get("tidal_id") or "").strip()
                    if tidal_id:
                        logger.info(
                            "tidarr: resolved tidal_id via artist catalog: %s "
                            "(artist_id=%s confidence=%.2f)",
                            tidal_id,
                            artist_id,
                            evaluated.get("match_confidence", 0.0),
                        )
                        return {"tidal_album_id": tidal_id}
            else:
                logger.debug(
                    "tidarr: artist catalog search found %d releases but no confident match "
                    "(best_confidence=%.2f status=%s)",
                    len(releases),
                    evaluated.get("match_confidence", 0.0),
                    evaluated.get("match_status"),
                )
        except Exception as e:
            logger.warning("tidarr: artist catalog resolution failed: %s", e)

        return None

    def _get_tidal_token(self) -> str | None:
        """Fetch fresh token from Tidarr settings. Never stored."""
        try:
            resp = self._session.get(f"{self._url}/api/settings", timeout=8)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("tiddl_config", {}).get("auth", {}).get("token", "")
            return token if token else None
        except Exception as e:
            logger.debug("tidarr: failed to fetch tidal token: %s", e)
            return None

    def _tidal_search(self, token: str, artist: str, album: str) -> list[dict]:
        """Call Tidal's native API search (not Newznab proxy)."""
        try:
            resp = requests.get(
                "https://api.tidal.com/v1/search",
                params={
                    "query": f"{artist} {album}",
                    "types": "ALBUMS",
                    "limit": 25,
                    "countryCode": "US",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("albums", {}).get("items", [])
        except Exception as e:
            logger.debug("tidarr: tidal search failed: %s", e)
            return []

    def _search_and_match_artist(self, token: str, artist_name: str) -> str | None:
        """
        Search for artist on Tidal and match by confidence.
        Returns artist ID if best match confidence >= 0.85.
        Uses popularity as tiebreaker for equal scores.
        """
        try:
            resp = requests.get(
                "https://api.tidal.com/v1/search",
                params={
                    "query": artist_name,
                    "types": "ARTISTS",
                    "limit": 25,
                    "countryCode": "US",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("artists", {}).get("items", [])

            if not results:
                logger.debug("tidarr: tidal artist search returned no results for '%s'", artist_name)
                return None

            # Score each artist result using match_album_title
            scored = []
            for result in results:
                result_name = str(result.get("name") or "")
                if not result_name:
                    continue

                # Reuse existing matcher for artist name scoring
                confidence = match_album_title(lib_title=artist_name, api_title=result_name)

                popularity = result.get("popularity", 0)
                scored.append({
                    "artist_id": result.get("id"),
                    "name": result_name,
                    "confidence": confidence,
                    "popularity": popularity,
                })

            # Sort by confidence (desc), then by popularity (desc)
            scored.sort(key=lambda x: (-x["confidence"], -x["popularity"]))
            best = scored[0]

            if best["confidence"] >= 0.85:
                logger.debug(
                    "tidarr: matched artist '%s' -> %s (confidence=%.2f popularity=%d)",
                    artist_name,
                    best["name"],
                    best["confidence"],
                    best["popularity"],
                )
                return str(best["artist_id"])
            else:
                logger.debug(
                    "tidarr: artist '%s' best match confidence too low: %.2f (threshold=0.85)",
                    artist_name,
                    best["confidence"],
                )
                return None

        except Exception as e:
            logger.warning("tidarr: artist search failed for '%s': %s", artist_name, e)
            return None

    def _get_artist_releases(
        self,
        token: str,
        artist_id: str,
        filter_type: str = "EPSANDSINGLES",
    ) -> list[dict]:
        """
        Query Tidal artist's complete release catalog with pagination.
        Supports EPSANDSINGLES, COMPILATIONS, etc.
        """
        all_releases = []
        offset = 0
        limit = 100

        try:
            while True:
                resp = requests.get(
                    f"https://api.tidal.com/v1/artists/{artist_id}/albums",
                    params={
                        "filter": filter_type,
                        "limit": limit,
                        "offset": offset,
                        "countryCode": "US",
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                items = data.get("items", [])
                if not items:
                    break

                all_releases.extend(items)

                # Check if we've retrieved all items
                total = data.get("totalNumberOfItems", 0)
                if offset + len(items) >= total:
                    break

                offset += limit

            logger.debug(
                "tidarr: fetched %d releases for artist_id %s (filter=%s)",
                len(all_releases),
                artist_id,
                filter_type,
            )
            return all_releases

        except Exception as e:
            logger.warning(
                "tidarr: failed to fetch artist releases for artist_id %s: %s",
                artist_id,
                e,
            )
            return []

    def _tidal_result_to_candidate(self, tidal_item: dict) -> dict:
        """Convert Tidal API album result to candidate dict for scoring."""
        return {
            "tidal_id": str(tidal_item.get("id", "")),
            "artist": str(tidal_item.get("artists", [{}])[0].get("name", "")),
            "album": str(tidal_item.get("title", "")),
            "year": str(tidal_item.get("releaseDate", ""))[:4],
            "track_count": tidal_item.get("numberOfTracks"),
            "quality": self._quality,
            "source": "tidal_api",
            "version": tidal_item.get("version"),
        }


PLUGIN_API_VERSION = 2
PLUGIN_VERSION = "2.2.0"
PLUGIN_DESCRIPTION = "Downloads albums via Tidarr (Tidal) with strict release matching."
CAPABILITIES = {
    "fetch_contract_version": 1,
    "pre_fetch_enrichment_version": 1,
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
