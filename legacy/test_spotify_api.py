from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
from core.config import CONFIG

def extract_album_id(s: str) -> str:
    s = s.strip()
    # spotify:album:<id>
    if s.startswith("spotify:album:"):
        return s.split(":")[-1]
    # https://open.spotify.com/album/<id>?...
    if "open.spotify.com/album/" in s:
        s = s.split("open.spotify.com/album/")[-1]
        s = s.split("?")[0].split("/")[0]
        return s
    # assume already an id
    return s

sp = Spotify(auth_manager=SpotifyClientCredentials(
    client_id=CONFIG["spotipy_client_id"],
    client_secret=CONFIG["spotipy_client_secret"],
))

#PUT_ID_OR_URL_OR_URI_HERE
ALBUM_INPUT = "5eimKvzlXDEgtQIE2fZJRR"
album_id = extract_album_id(ALBUM_INPUT)

a = sp.album(album_id)

print("id:", album_id)
print("name:", a.get("name"))
print("album_type:", a.get("album_type"))
print("release_date:", a.get("release_date"), "precision:", a.get("release_date_precision"))
print("artists:", [(x.get("name"), x.get("id")) for x in a.get("artists", [])])
print("url:", (a.get("external_urls") or {}).get("spotify"))
