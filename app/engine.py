"""
engine.py — candidate scoring and selection logic.

Pure functions: no DB access, no HTTP calls.
Input comes from soulsync_reader + last_fm_client, output goes to cc_store.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def score_candidate(
    track: dict,
    similar_artists_map: dict,
    lastfm_top_artists: dict,
    lastfm_loved_set: set,
) -> float:
    """
    Score a single discovery_pool track candidate.

    Inputs:
        track               — row from discovery_pool (dict)
        similar_artists_map — {artist_name: {occurrence_count, spotify_id}}
                              from soulsync_reader.get_similar_artists_map()
        lastfm_top_artists  — {artist_name: play_count}
                              from last_fm_client.get_top_artists()
        lastfm_loved_set    — set of artist names from loved tracks
                              from last_fm_client.get_loved_artist_names()

    Returns:
        float score (higher = stronger recommendation)
    """
    score = 0.0

    # --- Popularity signal (Spotify 0-100) ---
    popularity = track.get("popularity") or 0
    score += popularity * 0.4  # 0–40 pts

    # --- Taste graph signal (SoulSync similar_artists occurrence_count) ---
    artist = track.get("artist_name", "")
    occ = similar_artists_map.get(artist, {}).get("occurrence_count", 0)
    score += occ * 5.0  # +5 per watchlist artist that shares this similar artist

    # --- Last.fm personal signal ---
    lf_plays = lastfm_top_artists.get(artist, 0)
    score += min(lf_plays / 10.0, 20.0)  # up to +20 pts from play count

    # --- Explicit love bonus ---
    if artist in lastfm_loved_set:
        score += 15.0

    # --- Recency bonus ---
    if track.get("is_new_release"):
        score += 15.0

    return round(score, 2)


def score_candidates(
    tracks: list[dict],
    similar_artists_map: dict,
    lastfm_top_artists: dict,
    lastfm_loved_set: set,
) -> list[dict]:
    """
    Score all candidates and return them sorted highest-score first.
    Adds a 'score' key to each track dict.
    """
    scored = []
    for track in tracks:
        t = dict(track)
        t["score"] = score_candidate(t, similar_artists_map, lastfm_top_artists, lastfm_loved_set)
        scored.append(t)

    scored.sort(key=lambda x: x["score"], reverse=True)
    logger.debug("Scored %d candidates. Top score: %.1f", len(scored), scored[0]["score"] if scored else 0)
    return scored


def filter_owned(tracks: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Split tracks into (unowned, owned) based on is_owned flag.
    is_owned and plex_rating_key must be set before calling this.
    """
    unowned = [t for t in tracks if not t.get("is_owned")]
    owned = [t for t in tracks if t.get("is_owned") and t.get("plex_rating_key")]
    return unowned, owned


def select_top_n(scored_unowned: list[dict], max_n: int) -> list[dict]:
    """Return the top N unowned candidates for acquisition."""
    return scored_unowned[:max_n]


def apply_owned_check(tracks: list[dict], reader) -> list[dict]:
    """
    Run the two-tier owned-check on each candidate.
    Mutates each track dict: sets is_owned=True/False and plex_rating_key.

    reader — soulsync_reader module (passed in to keep this function testable)
    """
    for track in tracks:
        spotify_id = track.get("spotify_track_id")
        rating_key = reader.check_owned_exact(spotify_id) if spotify_id else None

        if rating_key:
            track["is_owned"] = True
            track["plex_rating_key"] = rating_key
        else:
            track["is_owned"] = False
            track["plex_rating_key"] = None

    owned_count = sum(1 for t in tracks if t.get("is_owned"))
    logger.debug("Owned-check: %d/%d tracks already in library", owned_count, len(tracks))
    return tracks


def build_taste_playlist(
    top_artists: dict,
    loved_set: set,
    artist_tracks: dict,
    limit: int = 50,
    max_per_artist: int = 2,
) -> list[dict]:
    """
    Build a scored playlist from owned library tracks weighted by Last.fm taste.

    Inputs (all resolved by the caller — this function is pure):
        top_artists    — {artist_name: play_count} from last_fm_client.get_top_artists()
        loved_set      — set of artist names from last_fm_client.get_loved_artist_names()
        artist_tracks  — {artist_name: [track_dicts from soulsync_reader.get_all_tracks_for_artist()]}
                         Each track dict must have: plex_rating_key, track_title, track_number,
                         album_title, album_year, album_thumb_url, spotify_track_id
        limit          — max tracks to return
        max_per_artist — max tracks from any single artist (default 2, ensures breadth)

    Scoring:
        play_count / 5.0       — listened-to artist weight (no cap)
        +15 if loved artist    — explicit love signal
        +10 if album_year >= current_year - 1  — recent release bonus

    Returns list of track dicts sorted by score desc, capped at limit with per-artist breadth.
    Each dict: track_name, artist_name, album_name, album_cover_url,
               plex_rating_key, spotify_track_id, score, position.
    """
    current_year = datetime.utcnow().year
    scored = []

    for artist_name, tracks in artist_tracks.items():
        play_count = top_artists.get(artist_name, 0)
        base_score = play_count / 5.0
        loved_bonus = 15.0 if artist_name in loved_set else 0.0

        for t in tracks:
            recency_bonus = 10.0 if (t.get("album_year") or 0) >= current_year - 1 else 0.0
            track_score = round(base_score + loved_bonus + recency_bonus, 2)
            scored.append({
                "track_name": t.get("track_title", ""),
                "artist_name": artist_name,
                "album_name": t.get("album_title", ""),
                "album_cover_url": t.get("album_thumb_url", ""),
                "plex_rating_key": t.get("plex_rating_key"),
                "spotify_track_id": t.get("spotify_track_id"),
                "score": track_score,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Apply per-artist cap for breadth, then overall limit
    artist_counts: dict[str, int] = {}
    result = []
    for track in scored:
        artist = track["artist_name"]
        if artist_counts.get(artist, 0) < max_per_artist:
            result.append(track)
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        if len(result) >= limit:
            break

    for i, t in enumerate(result):
        t["position"] = i

    logger.info(
        "build_taste_playlist: %d tracks from %d artists → top %d selected (max_per_artist=%d)",
        len(scored), len(artist_tracks), len(result), max_per_artist,
    )
    return result
