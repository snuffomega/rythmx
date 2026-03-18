"""
file_scanner.py — Direct File Scanner (stub, Phase 14+)

Future library platform backend. Ingests a music library directly from a
folder on disk — no media server required. Reads ID3v2 / FLAC / Opus tags.

When built:
  - Walk MUSIC_DIR recursively for .mp3 / .flac / .ogg / .opus / .m4a files
  - Parse tags via mutagen: title, artist, album, track_number, disc_number,
    duration, date_added, bitrate, container
  - INSERT into lib_* tables with source_platform='file'
  - Register in get_library_reader() as platform='file'

Dependency to add when implementing: mutagen
"""


class DirectFileScanner:
    """Stub. Not yet implemented (Phase 14+)."""

    def sync_library(self) -> dict:
        raise NotImplementedError("DirectFileScanner not yet implemented (Phase 14+)")

    def get_track_count(self) -> int:
        raise NotImplementedError
