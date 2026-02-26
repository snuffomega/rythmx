"""
soulsync_reader.py — SoulSync library backend (default).

Implements the library reader interface contract. To use a different backend,
set the LIBRARY_BACKEND env var and see app/db/__init__.py.

Interface contract (all backends must implement these with identical signatures):
  Identity:
    get_soulsync_artist_id(artist_name: str) -> str | None
    get_spotify_artist_id(artist_name: str) -> str | None
    get_deezer_artist_id(artist_name: str) -> str | None
    get_itunes_artist_id(artist_name: str) -> str | None

  Library queries:
    check_album_owned(artist_name, album_name, **ids) -> str | None  (plex_rating_key)
    check_owned_exact(spotify_track_id: str) -> str | None
    check_owned_deezer(deezer_track_id: str) -> str | None
    find_track_by_name(artist_name: str, track_title: str) -> str | None
    get_all_tracks_for_artist(artist_id: str) -> list[dict]
    get_tracks_for_album(artist_id: str, album_title: str) -> list[dict]
    get_discovery_pool(limit: int, ...) -> list[dict]
    get_similar_artists_map(limit: int) -> dict

  Health:
    is_db_accessible() -> bool

Opens the DB in read-only WAL mode (sqlite3 URI). Never writes.
No SoulSync Python imports. Pure sqlite3.
"""
import sqlite3
import logging
from app import config

logger = logging.getLogger(__name__)


def _connect():
    """Return a read-only connection to the SoulSync DB.

    immutable=1 bypasses WAL locking entirely — safe because the Docker mount
    is :ro and we never write. Prevents intermittent 'unable to open database
    file' errors when SoulSync checkpoints its WAL journal.
    """
    uri = f"file:{config.SOULSYNC_DB}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def get_discovery_pool(limit: int = 200, new_releases_only: bool = False, source: str = None) -> list[dict]:
    """
    Pull candidate tracks from SoulSync's discovery_pool table.
    Returns a list of dicts with all relevant scoring fields.
    """
    query = """
        SELECT
            id,
            spotify_track_id,
            spotify_artist_id,
            itunes_track_id,
            source,
            track_name,
            artist_name,
            album_name,
            album_cover_url,
            popularity,
            release_date,
            is_new_release,
            artist_genres,
            added_date
        FROM discovery_pool
        WHERE 1=1
    """
    params = []

    if new_releases_only:
        query += " AND is_new_release = 1"
    if source:
        query += " AND source = ?"
        params.append(source)

    query += " ORDER BY added_date DESC LIMIT ?"
    params.append(limit)

    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("soulsync_reader.get_discovery_pool failed: %s", e)
        return []


def check_owned_exact(spotify_track_id: str) -> str | None:
    """
    Tier 1 owned-check: exact match on spotify_track_id.
    Returns the SoulSync tracks.id (= Plex ratingKey) or None.
    """
    if not spotify_track_id:
        return None
    query = "SELECT id FROM tracks WHERE spotify_track_id = ? LIMIT 1"
    try:
        with _connect() as conn:
            row = conn.execute(query, (spotify_track_id,)).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error("soulsync_reader.check_owned_exact failed: %s", e)
        return None


def get_top_similar_artists(limit: int = 100) -> list[dict]:
    """
    Pull the taste graph from SoulSync's similar_artists table.
    Returns artists sorted by occurrence_count descending.
    """
    query = """
        SELECT
            similar_artist_name,
            similar_artist_spotify_id,
            occurrence_count
        FROM similar_artists
        ORDER BY occurrence_count DESC
        LIMIT ?
    """
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("soulsync_reader.get_top_similar_artists failed: %s", e)
        return []


def get_similar_artists_map(limit: int = 200) -> dict:
    """
    Return similar_artists as a dict keyed by artist name for O(1) lookup.
    { 'Artist Name': {'occurrence_count': 8, 'spotify_id': '...'}, ... }
    """
    rows = get_top_similar_artists(limit=limit)
    return {
        r["similar_artist_name"]: {
            "occurrence_count": r["occurrence_count"],
            "spotify_id": r.get("similar_artist_spotify_id"),
        }
        for r in rows
    }


def get_spotify_artist_id(artist_name: str) -> str | None:
    """
    Look up a Spotify artist ID from SoulSync's taste graph by artist name.
    Checks similar_artists first (richer data), then discovery_pool, then artists table.
    Returns the Spotify artist ID string or None.
    """
    if not artist_name:
        return None
    try:
        with _connect() as conn:
            # Check similar_artists first — has Spotify IDs for taste-graph artists
            row = conn.execute(
                "SELECT similar_artist_spotify_id FROM similar_artists "
                "WHERE lower(similar_artist_name) = lower(?) AND similar_artist_spotify_id IS NOT NULL "
                "LIMIT 1",
                (artist_name,)
            ).fetchone()
            if row and row[0]:
                return row[0]
            # Check discovery_pool
            row = conn.execute(
                "SELECT spotify_artist_id FROM discovery_pool "
                "WHERE lower(artist_name) = lower(?) AND spotify_artist_id IS NOT NULL "
                "LIMIT 1",
                (artist_name,)
            ).fetchone()
            if row and row[0]:
                return row[0]
            # Check artists table (enriched library artists)
            row = conn.execute(
                "SELECT spotify_artist_id FROM artists "
                "WHERE lower(name) = lower(?) AND spotify_artist_id IS NOT NULL "
                "LIMIT 1",
                (artist_name,)
            ).fetchone()
            return row[0] if row and row[0] else None
    except Exception as e:
        logger.error("soulsync_reader.get_spotify_artist_id failed: %s", e)
        return None


def get_deezer_artist_id(artist_name: str) -> str | None:
    """
    Look up a Deezer artist ID from SoulSync's enriched artists table.
    SoulSync stores this in artists.deezer_id (not deezer_artist_id).
    Returns the Deezer artist ID string or None if not found.
    """
    if not artist_name:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT deezer_id FROM artists "
                "WHERE lower(name) = lower(?) AND deezer_id IS NOT NULL "
                "LIMIT 1",
                (artist_name,)
            ).fetchone()
            return row[0] if row and row[0] else None
    except Exception as e:
        logger.debug("soulsync_reader.get_deezer_artist_id skipped (%s)", e)
        return None


def get_soulsync_artist_id(artist_name: str) -> str | None:
    """
    Look up SoulSync's internal artists.id for a given artist name.
    Returns the SoulSync artist PK (used for exact JOIN queries) or None.

    Tries exact case-insensitive match first. Falls back to norm() comparison
    to handle articles and punctuation differences (e.g. 'The 1975' → '1975').
    """
    if not artist_name:
        return None

    # Inline norm: NFKC + lowercase + strip leading articles + remove punctuation
    import unicodedata, re
    _articles = frozenset({"the", "a", "an"})
    def _norm(s):
        s = unicodedata.normalize("NFKC", s).lower()
        words = s.split()
        if words and words[0] in _articles:
            words = words[1:]
        s = " ".join(words)
        return re.sub(r"[^\w\s]", "", s).strip()

    norm_name = _norm(artist_name)
    try:
        with _connect() as conn:
            # Exact case-insensitive match (covers most cases)
            row = conn.execute(
                "SELECT id FROM artists WHERE lower(name) = lower(?) LIMIT 1",
                (artist_name,)
            ).fetchone()
            if row:
                return str(row[0])
            # Norm fallback: fetch all artist names and compare normalized forms
            rows = conn.execute("SELECT id, name FROM artists").fetchall()
            for r in rows:
                if _norm(r[1]) == norm_name:
                    return str(r[0])
            return None
    except Exception as e:
        logger.debug("soulsync_reader.get_soulsync_artist_id skipped (%s)", e)
        return None


def get_itunes_artist_id(artist_name: str) -> str | None:
    """
    Look up an iTunes artist ID from SoulSync's enriched artists table.
    SoulSync enriches artists.itunes_artist_id during library scanning.
    Returns the iTunes artist ID string or None if column absent or not found.
    """
    if not artist_name:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT itunes_artist_id FROM artists "
                "WHERE lower(name) = lower(?) AND itunes_artist_id IS NOT NULL "
                "LIMIT 1",
                (artist_name,)
            ).fetchone()
            return row[0] if row and row[0] else None
    except Exception as e:
        # Column may not exist on older SoulSync schema versions — not an error
        logger.debug("soulsync_reader.get_itunes_artist_id skipped (%s)", e)
        return None


def check_album_owned(artist_name: str, album_name: str,
                      soulsync_artist_id: str = None,
                      spotify_artist_id: str = None,
                      itunes_artist_id: str = None,
                      deezer_album_id: str = None,
                      spotify_album_id: str = None,
                      itunes_album_id: str = None) -> str | None:
    """
    Owned-check for a release (album/single) against SoulSync's tracks table.
    Returns the Plex ratingKey (tracks.id) of a matching track, or None if not owned.

    tracks has no artist_name/album_name columns — uses FK JOINs to artists/albums tables.
    SoulSync column names: artists.deezer_id, albums.deezer_id (not deezer_artist/album_id).

    Tier 0:  soulsync_artist_id PK + album title (exact internal ID join — most reliable)
    Tier 1a: itunes_album_id exact match against albums.itunes_album_id
    Tier 1b: deezer_album_id exact match against albums.deezer_id
    Tier 1c: spotify_album_id exact match against albums.spotify_album_id
    Tier 2a: itunes_artist_id + album title (reliable when Spotify not scanned)
    Tier 2b: spotify_artist_id + album title
    Tier 3:  artist name + album title text match (last resort)
    """
    if not album_name:
        return None
    try:
        with _connect() as conn:
            # Tier 0 — SoulSync internal artist ID (exact PK join, no ambiguity)
            if soulsync_artist_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE al.artist_id = ? AND lower(al.title) = lower(?) LIMIT 1",
                    (soulsync_artist_id, album_name)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 1a — iTunes album ID (exact)
            if itunes_album_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE al.itunes_album_id = ? LIMIT 1",
                    (itunes_album_id,)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 1b — Deezer album ID (exact) — SoulSync stores as albums.deezer_id
            if deezer_album_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE al.deezer_id = ? LIMIT 1",
                    (deezer_album_id,)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 1c — Spotify album ID (exact)
            if spotify_album_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE al.spotify_album_id = ? LIMIT 1",
                    (spotify_album_id,)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 2a — iTunes artist ID + album title (primary for non-Spotify installs)
            if itunes_artist_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN artists a ON t.artist_id = a.id "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE a.itunes_artist_id = ? AND lower(al.title) = lower(?) "
                    "LIMIT 1",
                    (itunes_artist_id, album_name)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 2b — Spotify artist ID + album title
            if spotify_artist_id:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN artists a ON t.artist_id = a.id "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE a.spotify_artist_id = ? AND lower(al.title) = lower(?) "
                    "LIMIT 1",
                    (spotify_artist_id, album_name)
                ).fetchone()
                if row:
                    return row[0]

            # Tier 3 — artist name + album title text match
            if artist_name:
                row = conn.execute(
                    "SELECT t.id FROM tracks t "
                    "JOIN artists a ON t.artist_id = a.id "
                    "JOIN albums al ON t.album_id = al.id "
                    "WHERE lower(a.name) = lower(?) AND lower(al.title) = lower(?) "
                    "LIMIT 1",
                    (artist_name, album_name)
                ).fetchone()
                return row[0] if row else None

        return None
    except Exception as e:
        logger.error("soulsync_reader.check_album_owned failed: %s", e)
        return None


def get_all_tracks_for_artist(soulsync_artist_id: str) -> list[dict]:
    """
    Return all tracks in the SoulSync library for a given artist PK.
    Used by the taste-playlist builder to pull owned tracks per Last.fm artist.

    Returns list of dicts with: plex_rating_key, track_title, track_number,
    spotify_track_id, album_title, album_year, album_thumb_url.
    """
    if not soulsync_artist_id:
        return []
    query = """
        SELECT
            t.id            AS plex_rating_key,
            t.title         AS track_title,
            t.track_number,
            t.spotify_track_id,
            al.title        AS album_title,
            al.year         AS album_year,
            al.thumb_url    AS album_thumb_url
        FROM tracks t
        JOIN albums al ON t.album_id = al.id
        WHERE al.artist_id = ?
        ORDER BY al.year DESC, t.track_number
    """
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, (soulsync_artist_id,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("soulsync_reader.get_all_tracks_for_artist failed: %s", e)
        return []


def find_track_by_name(artist_name: str, track_title: str) -> str | None:
    """
    Look up a track's plex_rating_key (tracks.id) by artist name + track title.
    Used as Tier 2 fallback when matching external playlist tracks against the library.

    Tier 2a: exact case-insensitive SQL match (fast, covers ASCII apostrophes)
    Tier 2b: NFKC-normalized Python-side fallback (handles unicode apostrophes,
             smart quotes, feat./ft. suffixes — e.g. U+2019 vs U+0027)

    Returns the track id or None.
    """
    if not artist_name or not track_title:
        return None

    # Inline _norm: mirrors get_soulsync_artist_id() and music_client.norm()
    import unicodedata, re as _re

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s).lower()
        # Strip feat./ft./featuring (parenthetical or trailing)
        s = _re.sub(
            r'\s*[\(\[][^)\]]*(?:feat|ft|featuring)[.\s][^\)\]]*[\)\]]',
            "", s, flags=_re.IGNORECASE
        )
        s = _re.sub(r'\s*(?:feat|ft|featuring)[.\s].*$', "", s, flags=_re.IGNORECASE)
        # Remove all non-alphanumeric characters (apostrophes, dashes, unicode variants)
        s = _re.sub(r'[^\w\s]', '', s)
        return _re.sub(r'\s+', ' ', s).strip()

    try:
        with _connect() as conn:
            # Tier 2a — exact case-insensitive SQL (fast, covers the majority of cases)
            row = conn.execute(
                "SELECT t.id FROM tracks t "
                "JOIN artists a ON t.artist_id = a.id "
                "WHERE lower(a.name) = lower(?) AND lower(t.title) = lower(?) "
                "LIMIT 1",
                (artist_name, track_title)
            ).fetchone()
            if row:
                return row[0]

            # Tier 2b — normalized Python-side fallback
            # Fetch all artists, find those with matching normalized name, then check titles
            norm_artist = _norm(artist_name)
            norm_title = _norm(track_title)
            if not norm_artist or not norm_title:
                return None

            artist_rows = conn.execute("SELECT id, name FROM artists").fetchall()
            matched_artist_ids = [
                r[0] for r in artist_rows if _norm(r[1]) == norm_artist
            ]
            if not matched_artist_ids:
                return None

            placeholders = ",".join("?" * len(matched_artist_ids))
            track_rows = conn.execute(
                f"SELECT t.id, t.title FROM tracks t "
                f"WHERE t.artist_id IN ({placeholders})",
                matched_artist_ids
            ).fetchall()
            for track_row in track_rows:
                if _norm(track_row[1]) == norm_title:
                    return track_row[0]

            return None
    except Exception as e:
        logger.debug("soulsync_reader.find_track_by_name failed: %s", e)
        return None


def get_tracks_for_album(soulsync_artist_id: str, album_title: str) -> list[dict]:
    """
    Return all tracks for a specific owned album.
    Filtered variant of get_all_tracks_for_artist() — scoped to one album by title.

    Used by the CC Playlist/Cruise mode to expand owned releases into individual tracks.
    Returns list of dicts with: plex_rating_key, track_title, track_number,
    spotify_track_id, album_title, album_year, album_thumb_url.
    """
    if not soulsync_artist_id or not album_title:
        return []
    query = """
        SELECT
            t.id            AS plex_rating_key,
            t.title         AS track_title,
            t.track_number,
            t.spotify_track_id,
            al.title        AS album_title,
            al.year         AS album_year,
            al.thumb_url    AS album_thumb_url
        FROM tracks t
        JOIN albums al ON t.album_id = al.id
        WHERE al.artist_id = ? AND lower(al.title) = lower(?)
        ORDER BY t.track_number
    """
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, (soulsync_artist_id, album_title)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("soulsync_reader.get_tracks_for_album failed: %s", e)
        return []


def check_owned_deezer(deezer_track_id: str) -> str | None:
    """
    Tier 1 Deezer-ID owned-check.
    Matches against tracks.deezer_id (populated by SoulSync when it resolves via Deezer catalog).
    Returns tracks.id (Plex ratingKey) or None.
    Tracks indexed only via iTunes will have deezer_id = NULL — falls through to Tier 2.
    """
    if not deezer_track_id:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE deezer_id = ? LIMIT 1",
                (str(deezer_track_id),)
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug("soulsync_reader.check_owned_deezer failed: %s", e)
        return None


def is_db_accessible() -> bool:
    """Health check — verify the SoulSync DB file can be opened."""
    try:
        with _connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception as e:
        logger.warning("SoulSync DB not accessible: %s", e)
        return False


def get_track_count() -> int:
    """Return total track count from SoulSync DB."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def sync_library() -> dict:
    """No-op for SoulSync backend — SoulSync manages its own DB.

    Present for interface parity with plex_reader and other backends.
    """
    logger.info("sync_library() called on SoulSync backend — no action needed")
    return {"message": "SoulSync manages its own library database"}
