#!/usr/bin/env python3
"""
Diagnostic script to test Tidal API album filters.
Tests what Wheeland Brothers releases are returned by different filters.
"""
import os
import requests
import json

# Get token from Tidarr
def get_tidal_token():
    tidarr_url = os.environ.get("TIDARR_URL", "http://tidarr:3030")
    try:
        resp = requests.get(f"{tidarr_url}/api/settings", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("tiddl_config", {}).get("auth", {}).get("token", "")
        return token if token else None
    except Exception as e:
        print(f"Failed to fetch token: {e}")
        return None

def search_artist(token, artist_name):
    """Search for artist on Tidal."""
    resp = requests.get(
        "https://api.tidal.com/v1/search",
        params={
            "query": artist_name,
            "types": "ARTISTS",
            "limit": 5,
            "countryCode": "US",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("artists", {}).get("items", [])

def get_releases(token, artist_id, filter_type=None):
    """Get artist releases with optional filter."""
    params = {
        "limit": 100,
        "countryCode": "US",
    }
    if filter_type:
        params["filter"] = filter_type

    resp = requests.get(
        f"https://api.tidal.com/v1/artists/{artist_id}/albums",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])

def main():
    token = get_tidal_token()
    if not token:
        print("ERROR: Could not get Tidal token")
        return

    print("Searching for Wheeland Brothers...")
    artists = search_artist(token, "Wheeland Brothers")
    if not artists:
        print("ERROR: Artist not found")
        return

    artist = artists[0]
    artist_id = artist.get("id")
    artist_name = artist.get("name")
    print(f"Found: {artist_name} (ID: {artist_id})\n")

    # Test different filters
    filters = [None, "ALBUMS", "EPSANDSINGLES", "COMPILATIONS"]

    for filter_type in filters:
        filter_label = filter_type or "NO FILTER"
        print(f"\n{'='*60}")
        print(f"Filter: {filter_label}")
        print(f"{'='*60}")

        try:
            releases = get_releases(token, artist_id, filter_type)
            print(f"Total releases: {len(releases)}\n")

            # Group by type
            albums = {}
            singles = {}
            eps = {}

            for r in releases:
                title = r.get("title", "?")
                rel_type = r.get("releaseType", "UNKNOWN")
                track_count = r.get("numberOfTracks", 0)

                info = f"{title} (tracks: {track_count})"

                if rel_type == "Album":
                    albums[title] = info
                elif rel_type == "Single":
                    singles[title] = info
                elif rel_type == "EP":
                    eps[title] = info
                else:
                    eps[title] = f"{info} [TYPE: {rel_type}]"

            if albums:
                print("ALBUMS:")
                for title, info in sorted(albums.items()):
                    print(f"  • {info}")

            if singles:
                print("\nSINGLES:")
                for title, info in sorted(singles.items()):
                    print(f"  • {info}")

            if eps:
                print("\nEPs:")
                for title, info in sorted(eps.items()):
                    print(f"  • {info}")

        except Exception as e:
            print(f"ERROR: {e}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print("If NO FILTER and EPSANDSINGLES return the same count,")
    print("then EPSANDSINGLES includes all release types.")
    print("\nIf they differ, we may need to query multiple filters.")

if __name__ == "__main__":
    main()
