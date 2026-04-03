"""
musicbrainz_client.py — MusicBrainz API client for enrichment.

Docs: https://musicbrainz.org/doc/MusicBrainz_API
Rate limit: 1 req/s without auth; we use 50 RPM (conservative).
User-Agent is mandatory per MB ToS.
"""
import logging
import requests

from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)

_BASE_URL = "https://musicbrainz.org/ws/2"
_HEADERS = {
    "User-Agent": "rythmx/1.0 (https://github.com/snuffomega/rythmx)",
    "Accept": "application/json",
}

_session = requests.Session()
_session.headers.update(_HEADERS)


def search_artist(name: str, limit: int = 5) -> list[dict]:
    """Search MusicBrainz for artist candidates.

    Returns [{mbid, name, disambiguation, area, score}, ...].
    """
    rate_limiter.acquire("musicbrainz")
    try:
        resp = _session.get(
            f"{_BASE_URL}/artist",
            params={"query": f'artist:"{name}"', "limit": limit, "fmt": "json"},
            timeout=15,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("musicbrainz")
            return []
        resp.raise_for_status()
        data = resp.json()
        rate_limiter.record_success("musicbrainz")

        results = []
        for a in data.get("artists", []):
            results.append({
                "mbid": a.get("id", ""),
                "name": a.get("name", ""),
                "disambiguation": a.get("disambiguation", ""),
                "area": a.get("area", {}).get("name", "") if a.get("area") else "",
                "score": a.get("score", 0),
            })
        return results
    except requests.RequestException as e:
        logger.error("MusicBrainz search failed for '%s': %s", name, type(e).__name__)
        return []


def get_artist(mbid: str) -> dict | None:
    """Fetch full artist details by MBID.

    Returns {mbid, name, area, begin_area, formed_year, type, disambiguation}
    or None on error.
    """
    rate_limiter.acquire("musicbrainz")
    try:
        resp = _session.get(
            f"{_BASE_URL}/artist/{mbid}",
            params={"fmt": "json"},
            timeout=15,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("musicbrainz")
            return None
        if resp.status_code == 404:
            rate_limiter.record_success("musicbrainz")
            return None
        resp.raise_for_status()
        data = resp.json()
        rate_limiter.record_success("musicbrainz")

        life_span = data.get("life-span", {})
        begin = life_span.get("begin", "")
        formed_year = None
        if begin:
            try:
                formed_year = int(begin[:4])
            except (ValueError, IndexError):
                pass

        return {
            "mbid": data.get("id", mbid),
            "name": data.get("name", ""),
            "area": data.get("area", {}).get("name", "") if data.get("area") else "",
            "begin_area": data.get("begin-area", {}).get("name", "") if data.get("begin-area") else "",
            "formed_year": formed_year,
            "type": data.get("type", ""),
            "disambiguation": data.get("disambiguation", ""),
        }
    except requests.RequestException as e:
        logger.error("MusicBrainz get_artist failed for '%s': %s", mbid, type(e).__name__)
        return None


def get_release(release_mbid: str) -> dict | None:
    """Fetch a specific release and its release group to get first-release-date.

    Calls /ws/2/release/{id}?inc=release-groups&fmt=json.
    Returns {"release_group_id": str, "first_release_date": str} or None.
    """
    rate_limiter.acquire("musicbrainz")
    try:
        resp = _session.get(
            f"{_BASE_URL}/release/{release_mbid}",
            params={"inc": "release-groups", "fmt": "json"},
            timeout=15,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("musicbrainz")
            return None
        if resp.status_code == 404:
            rate_limiter.record_success("musicbrainz")
            return None
        resp.raise_for_status()
        data = resp.json()
        rate_limiter.record_success("musicbrainz")

        rg = data.get("release-group") or {}
        release_group_id = rg.get("id", "")
        first_release_date = rg.get("first-release-date", "")

        if not release_group_id:
            return None

        return {
            "release_group_id": release_group_id,
            "first_release_date": first_release_date or None,
        }
    except requests.RequestException as e:
        logger.error("MusicBrainz get_release failed for '%s': %s", release_mbid, type(e).__name__)
        return None


def get_artist_release_groups(mbid: str, limit: int = 50) -> list[str]:
    """Fetch release group titles for album-overlap validation.

    Returns list of album title strings.
    """
    rate_limiter.acquire("musicbrainz")
    try:
        resp = _session.get(
            f"{_BASE_URL}/release-group",
            params={
                "artist": mbid,
                "type": "album",
                "limit": limit,
                "fmt": "json",
            },
            timeout=15,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("musicbrainz")
            return []
        resp.raise_for_status()
        data = resp.json()
        rate_limiter.record_success("musicbrainz")

        return [rg.get("title", "") for rg in data.get("release-groups", []) if rg.get("title")]
    except requests.RequestException as e:
        logger.error("MusicBrainz release-groups failed for '%s': %s", mbid, type(e).__name__)
        return []


def browse_artist_release_groups(mbid: str, limit: int = 50) -> list[dict]:
    """Fetch release groups for an artist, returning full detail for album enrichment.

    Unlike get_artist_release_groups() (which returns title strings for overlap
    validation), this returns dicts with id, title, and first-release-date so
    callers can write musicbrainz_release_group_id and original_release_date.

    Returns [{id, title, first_release_date}, ...] or [] on error.
    """
    rate_limiter.acquire("musicbrainz")
    try:
        resp = _session.get(
            f"{_BASE_URL}/release-group",
            params={
                "artist": mbid,
                "type": "album",
                "limit": limit,
                "fmt": "json",
            },
            timeout=15,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("musicbrainz")
            return []
        resp.raise_for_status()
        data = resp.json()
        rate_limiter.record_success("musicbrainz")

        results = []
        for rg in data.get("release-groups", []):
            rg_id = rg.get("id", "")
            title = rg.get("title", "")
            first_date = rg.get("first-release-date", "") or None
            if rg_id and title:
                results.append({"id": rg_id, "title": title, "first_release_date": first_date})
        return results
    except requests.RequestException as e:
        logger.error("MusicBrainz browse release-groups failed for '%s': %s", mbid, type(e).__name__)
        return []
