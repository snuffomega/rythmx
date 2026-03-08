# test_spotify_api.py is a manual script (not a pytest test).
# It calls sys.exit(1) at module level when Spotify creds are absent,
# which crashes the entire pytest session. Exclude it from collection.
collect_ignore = ["test_spotify_api.py"]
