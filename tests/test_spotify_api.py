"""
test_spotify_api.py — Manual Spotify API test + raw capture script.

Run with: python tests/test_spotify_api.py

Tests real API calls against known artists, validates response shapes,
writes raw JSON to spotify_raw_cache, and prints a summary of what
Spotify provides vs iTunes (unique data).

Not a pytest test — run directly. Requires SPOTIFY_CLIENT_ID and
SPOTIFY_CLIENT_SECRET in .env.
"""
import sys
import os
import json
import sqlite3

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config

# --- Config check ---
if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
    print("ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET not set in .env")
    sys.exit(1)

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=config.SPOTIFY_CLIENT_ID,
    client_secret=config.SPOTIFY_CLIENT_SECRET,
))

# Suppress spotipy debug logs during test
import logging
logging.getLogger("spotipy").setLevel(logging.WARNING)
logging.getLogger("spotipy.client").setLevel(logging.WARNING)

TEST_ARTIST = "Black Pistol Fire"
PASS = "✅"
FAIL = "❌"
results = []


def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, label, detail))
    print(f"  {status} {label}" + (f" — {detail}" if detail else ""))


print(f"\n{'='*60}")
print(f"  Spotify API Test — {TEST_ARTIST}")
print(f"{'='*60}\n")

# --- Test 1: Artist search ---
print("[ Artist Search ]")
search_result = sp.search(q=f'artist:"{TEST_ARTIST}"', type="artist", limit=5)
artists = search_result.get("artists", {}).get("items", [])
check("Search returns results", len(artists) > 0, f"{len(artists)} results")

if not artists:
    print("\nFATAL: no artists found — aborting")
    sys.exit(1)

artist = next((a for a in artists if a["name"].lower() == TEST_ARTIST.lower()), artists[0])
spotify_artist_id = artist["id"]
check("Artist name match", artist["name"].lower() == TEST_ARTIST.lower(), artist["name"])
check("Artist ID present", bool(spotify_artist_id), spotify_artist_id)

# --- Test 2: Full artist object ---
print("\n[ Full Artist Object ]")
artist_data = sp.artist(spotify_artist_id)
genres = artist_data.get("genres", [])
popularity = artist_data.get("popularity")
images = artist_data.get("images", [])
check("Genres present", len(genres) > 0, str(genres))
check("Popularity score", popularity is not None, str(popularity))
check("Artist images", len(images) > 0, f"{len(images)} sizes")
check("Followers count", artist_data.get("followers", {}).get("total", 0) > 0,
      str(artist_data.get("followers", {}).get("total")))

# --- Test 3: Artist albums ---
print("\n[ Artist Albums (own discography) ]")
albums_data = sp.artist_albums(spotify_artist_id, include_groups="album,single", limit=10)
albums = albums_data.get("items", [])
check("Albums returned", len(albums) > 0, f"{len(albums)} albums/singles")
if albums:
    sample = albums[0]
    check("Album has spotify_album_id", bool(sample.get("id")), sample.get("id", "")[:20])
    check("Album has release_date", bool(sample.get("release_date")), sample.get("release_date"))
    check("Album has album_type", bool(sample.get("album_type")), sample.get("album_type"))

# --- Test 4: Appears_on (collabs/features) ---
print("\n[ Appears On (collabs/features) ]")
appears_data = sp.artist_albums(spotify_artist_id, include_groups="appears_on", limit=10)
appears_items = appears_data.get("items", [])
check("Appears_on endpoint works", True, f"{len(appears_items)} collab albums found")

# --- Test 5: Audio features ---
print("\n[ Audio Features ]")
# Get a track ID from the first album
track_id = None
if albums:
    tracks_resp = sp.album_tracks(albums[0]["id"], limit=1)
    track_items = tracks_resp.get("items", [])
    if track_items:
        track_id = track_items[0]["id"]

if track_id:
    try:
        features = sp.audio_features([track_id])
        if features and features[0]:
            f = features[0]
            audio_keys = ["energy", "valence", "danceability", "tempo", "acousticness",
                          "instrumentalness", "speechiness", "loudness"]
            for key in audio_keys:
                check(f"audio_features.{key}", key in f, str(round(f.get(key, 0), 3)))
        else:
            check("Audio features returned", False, "empty response")
    except Exception as e:
        msg = str(e)
        if "403" in msg:
            print("  ⚠️  audio_features — SKIPPED (Spotify removed this endpoint for new apps, Nov 2024)")
        else:
            check("Audio features", False, msg[:80])
else:
    check("Audio features", False, "no track ID available to test")

# --- Test 6: Write to spotify_raw_cache ---
print("\n[ Raw Cache Write ]")
try:
    conn = sqlite3.connect(config.RYTHMX_DB)
    conn.execute("""
        INSERT OR REPLACE INTO spotify_raw_cache
            (query_type, entity_id, entity_name, raw_json, fetched_at)
        VALUES ('artist', ?, ?, ?, datetime('now'))
    """, (spotify_artist_id, TEST_ARTIST, json.dumps(artist_data)))
    conn.execute("""
        INSERT OR REPLACE INTO spotify_raw_cache
            (query_type, entity_id, entity_name, raw_json, fetched_at)
        VALUES ('appears_on', ?, ?, ?, datetime('now'))
    """, (spotify_artist_id, TEST_ARTIST, json.dumps(appears_data)))
    conn.commit()
    row = conn.execute(
        "SELECT COUNT(*) FROM spotify_raw_cache WHERE entity_id = ?",
        (spotify_artist_id,)
    ).fetchone()
    conn.close()
    check("Raw cache rows written", row[0] >= 2, f"{row[0]} rows for {spotify_artist_id}")
except Exception as e:
    check("Raw cache write", False, str(e))

# --- Summary ---
print(f"\n{'='*60}")
print("  Summary")
print(f"{'='*60}")
passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
print(f"  {PASS} {passed} passed   {FAIL} {failed} failed\n")

print("  What Spotify provides uniquely:")
print(f"    Genres:     {genres}")
print(f"    Popularity: {popularity}/100")
print(f"    Appears_on: {len(appears_items)} collab albums")
print(f"    Artist ID:  {spotify_artist_id}")
if images:
    print(f"    Image URL:  {images[0]['url'][:60]}...")
print()

if failed > 0:
    sys.exit(1)
