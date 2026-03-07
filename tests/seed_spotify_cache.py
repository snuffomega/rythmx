"""
seed_spotify_cache.py — Seed spotify_raw_cache from your Last.fm top artists.

Fetches your top N Last.fm artists, looks each up on Spotify, writes full
artist JSON + appears_on JSON to spotify_raw_cache. Useful for building a
representative sample dataset without needing a full library sync.

Run with: python tests/seed_spotify_cache.py [--limit 25] [--period 6month]

Requires LASTFM_API_KEY, LASTFM_USERNAME, SPOTIFY_CLIENT_ID,
SPOTIFY_CLIENT_SECRET in .env.
"""
import sys
import os
import json
import sqlite3
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config

# --- Arg parsing ---
parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=25, help="Number of top artists to seed (default 25)")
parser.add_argument("--period", default="6month",
                    choices=["overall", "12month", "6month", "3month", "1month", "7day"],
                    help="Last.fm period (default 6month)")
args = parser.parse_args()

# --- Credential check ---
missing = []
if not config.LASTFM_API_KEY or not config.LASTFM_USERNAME:
    missing.append("LASTFM_API_KEY / LASTFM_USERNAME")
if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
    missing.append("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
if missing:
    print(f"ERROR: missing credentials: {', '.join(missing)}")
    sys.exit(1)

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
logging.getLogger("spotipy").setLevel(logging.WARNING)
logging.getLogger("spotipy.client").setLevel(logging.WARNING)

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=config.SPOTIFY_CLIENT_ID,
    client_secret=config.SPOTIFY_CLIENT_SECRET,
))

from app.clients import last_fm_client
from app.clients.music_client import _spotify_rate_limit, norm

# --- Fetch Last.fm top artists ---
print(f"\nFetching top {args.limit} artists from Last.fm (period={args.period})...")
top = last_fm_client.get_top_artists(period=args.period, limit=args.limit)
artists = list(top.items())[:args.limit]  # [(name, playcount), ...]
print(f"Got {len(artists)} artists.\n")

# --- Open DB ---
conn = sqlite3.connect(config.RYTHMX_DB)

# --- Process each artist ---
hit = 0
miss = 0
skip = 0
errors = 0

print(f"{'Artist':<35} {'Plays':>6}  {'Spotify ID':<25} {'Genres'}")
print("-" * 95)

for artist_name, playcount in artists:
    # Check if already cached
    existing = conn.execute(
        "SELECT entity_id FROM spotify_raw_cache WHERE query_type='artist' AND entity_name=?",
        (artist_name,)
    ).fetchone()
    if existing:
        print(f"  {'[cached]':<33} {playcount:>6}  {existing[0]:<25} —")
        skip += 1
        continue

    try:
        _spotify_rate_limit()
        results = sp.search(q=f'artist:"{artist_name}"', type="artist", limit=5)
        items = results.get("artists", {}).get("items", [])

        if not items:
            print(f"  {'[no match] ' + artist_name:<35} {playcount:>6}  {'—':<25}")
            miss += 1
            continue

        match = next((a for a in items if norm(a["name"]) == norm(artist_name)), items[0])
        spotify_artist_id = match["id"]

        # Full artist object
        _spotify_rate_limit()
        artist_data = sp.artist(spotify_artist_id)
        genres = artist_data.get("genres", [])
        popularity = artist_data.get("popularity", 0)

        # Appears_on
        _spotify_rate_limit()
        appears_on_data = sp.artist_albums(
            spotify_artist_id, include_groups="appears_on", limit=20
        )
        appears_count = len(appears_on_data.get("items", []))

        # Write to cache
        conn.execute(
            """
            INSERT OR REPLACE INTO spotify_raw_cache
                (query_type, entity_id, entity_name, raw_json, fetched_at)
            VALUES ('artist', ?, ?, ?, datetime('now'))
            """,
            (spotify_artist_id, artist_name, json.dumps(artist_data)),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO spotify_raw_cache
                (query_type, entity_id, entity_name, raw_json, fetched_at)
            VALUES ('appears_on', ?, ?, ?, datetime('now'))
            """,
            (spotify_artist_id, artist_name, json.dumps(appears_on_data)),
        )
        conn.commit()

        genre_str = ", ".join(genres[:3]) if genres else "—"
        print(f"  {artist_name:<35} {playcount:>6}  {spotify_artist_id:<25} {genre_str}")
        hit += 1

    except Exception as e:
        msg = str(e)
        if "429" in msg or "rate" in msg.lower():
            print(f"  ⚠️  Rate limit hit on '{artist_name}' — stopping early")
            break
        print(f"  ❌ {artist_name}: {msg[:60]}")
        errors += 1

conn.close()

# --- Summary ---
total_rows = hit * 2  # artist + appears_on per artist
print(f"\n{'='*60}")
print(f"  Done — {hit} hits · {miss} misses · {skip} cached · {errors} errors")
print(f"  {total_rows} rows written to spotify_raw_cache")
print(f"{'='*60}\n")
