"""
tag_enrichment.py — Stage 1.1: read bitrate / codec / container from local music files.

Targets lib_tracks WHERE codec IS NULL AND file_path IS NOT NULL
  AND source_platform = 'navidrome'.

file_path values from Navidrome are relative paths (e.g. '311/311/01-01 - Down.flac').
The absolute path is resolved as:  MUSIC_DIR / file_path

Skipped entirely (returns immediately with zeros) when MUSIC_DIR is not configured.
Plex file paths are never read — they may not be accessible from the Rythmx container.

mutagen usage:
  mutagen.File(path, easy=False)  — auto-detects format, returns None for unrecognised files
  info.bitrate  — bits per second (divide by 1000 → kbps stored as int)
  type(audio)   — used to derive codec label
  os.path.splitext — derives container from extension
"""
import logging
import os

from app.db.rythmx_store import _connect

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Codec map: mutagen type name → canonical codec label
# --------------------------------------------------------------------------
_CODEC_MAP: dict[str, str] = {
    "MP3": "MP3",
    "FLAC": "FLAC",
    "MP4": "AAC",
    "OggVorbis": "OGG",
    "OggOpus": "OPUS",
    "OggFLAC": "FLAC",
    "AIFF": "AIFF",
    "ASF": "WMA",
    "WavPack": "WV",
    "MonkeysAudio": "APE",
    "WAVE": "WAV",
    "TrueAudio": "TTA",
    "Musepack": "MPC",
    "OptimFROG": "OFR",
    "Speex": "SPX",
}

# Extension → container (normalised, no dot)
_EXT_CONTAINER: dict[str, str] = {
    ".flac": "flac",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".m4b": "m4b",
    ".mp4": "mp4",
    ".aac": "aac",
    ".ogg": "ogg",
    ".opus": "opus",
    ".wma": "wma",
    ".aiff": "aiff",
    ".aif": "aiff",
    ".wav": "wav",
    ".wv": "wv",
    ".ape": "ape",
    ".tta": "tta",
    ".mpc": "mpc",
    ".spx": "spx",
}


def _extract_tags(filepath: str) -> dict | None:
    """
    Open a music file with mutagen and extract bitrate / codec / container
    plus replay_gain_track / embedded_lyrics / tag_genre.

    Returns a dict with keys: bitrate (int kbps), codec (str), container (str),
    replay_gain_track (float | None), embedded_lyrics (str | None), tag_genre (str | None).
    Returns None if the file is unreadable or mutagen cannot parse it.
    """
    import mutagen
    import mutagen.id3

    try:
        audio = mutagen.File(filepath, easy=False)
    except Exception as exc:
        logger.warning("tag_enrichment: mutagen error reading %s: %s", filepath, exc)
        return None

    if audio is None:
        logger.debug("tag_enrichment: mutagen returned None for %s", filepath)
        return None

    # --- bitrate (bps → kbps) ---
    bitrate: int | None = None
    try:
        bps = getattr(audio.info, "bitrate", None)
        if bps and bps > 0:
            bitrate = int(bps // 1000)
    except Exception:
        pass

    # --- codec ---
    type_name = type(audio).__name__
    codec: str | None = _CODEC_MAP.get(type_name)
    if codec is None and type_name:
        # Fall back: capitalise the raw type name (e.g. unknown future formats)
        codec = type_name.upper()

    # --- container ---
    _, ext = os.path.splitext(filepath)
    container: str | None = _EXT_CONTAINER.get(ext.lower())
    if container is None and ext:
        container = ext.lstrip(".").lower()

    # --- replay_gain_track (float, e.g. -2.72) ---
    replay_gain_track: float | None = None
    try:
        rg_raw: str | None = None
        tags = getattr(audio, "tags", audio)  # ID3 stores tags in audio.tags; Vorbis in audio
        if tags is not None:
            # ID3: TXXX frame with description REPLAYGAIN_TRACK_GAIN
            for key in ("TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:replaygain_track_gain"):
                frame = tags.get(key)
                if frame is not None:
                    text_list = getattr(frame, "text", None)
                    if text_list:
                        rg_raw = str(text_list[0]).strip()
                    break
            if rg_raw is None:
                # Vorbis comment: replaygain_track_gain = "-2.72 dB"
                values = audio.get("replaygain_track_gain", []) if hasattr(audio, "get") else []
                if values:
                    rg_raw = str(values[0]).strip()
        if rg_raw:
            # Strip " dB" suffix if present and parse float
            replay_gain_track = float(rg_raw.lower().replace("db", "").strip())
    except Exception:
        replay_gain_track = None

    # --- embedded_lyrics (plain text) ---
    embedded_lyrics: str | None = None
    try:
        tags = getattr(audio, "tags", audio)
        if tags is not None:
            # ID3: USLT frame — iterate because language suffix varies (e.g. USLT::eng)
            for key, frame in (tags.items() if hasattr(tags, "items") else []):
                if key.startswith("USLT"):
                    text = getattr(frame, "text", None)
                    if text:
                        embedded_lyrics = str(text).strip() or None
                    break
        if embedded_lyrics is None:
            # Vorbis comment: lyrics or unsyncedlyrics
            for vkey in ("lyrics", "unsyncedlyrics"):
                values = audio.get(vkey, []) if hasattr(audio, "get") else []
                if values:
                    embedded_lyrics = str(values[0]).strip() or None
                    break
    except Exception:
        embedded_lyrics = None

    # --- tag_genre (first genre string) ---
    tag_genre: str | None = None
    try:
        tags = getattr(audio, "tags", audio)
        if tags is not None:
            # ID3: TCON frame
            frame = tags.get("TCON") if hasattr(tags, "get") else None
            if frame is not None:
                text_list = getattr(frame, "text", None)
                if text_list:
                    tag_genre = str(text_list[0]).strip() or None
        if tag_genre is None:
            # Vorbis comment
            values = audio.get("genre", []) if hasattr(audio, "get") else []
            if values:
                tag_genre = str(values[0]).strip() or None
    except Exception:
        tag_genre = None

    return {
        "bitrate": bitrate,
        "codec": codec,
        "container": container,
        "replay_gain_track": replay_gain_track,
        "embedded_lyrics": embedded_lyrics,
        "tag_genre": tag_genre,
    }


def enrich_tags(batch_size: int = 50, stop_event=None, on_progress=None) -> dict:
    """
    Stage 1.1 — tag enrichment pass.

    Reads bitrate, codec, container, replay_gain_track, embedded_lyrics,
    and tag_genre from local music files using mutagen.
    Only processes Navidrome tracks (source_platform = 'navidrome') where
    codec IS NULL and file_path IS NOT NULL.

    Guarded: returns immediately (all zeros) when MUSIC_DIR is not set.

    Returns {"processed": N, "skipped": N, "errors": N}.
    """
    from app.config import MUSIC_DIR

    if not MUSIC_DIR:
        logger.info("tag_enrichment: MUSIC_DIR not set — skipping")
        return {"processed": 0, "skipped": 0, "errors": 0}

    music_dir = MUSIC_DIR.rstrip("/\\")

    processed = 0
    skipped = 0
    errors = 0
    missing_by_dir: dict[str, int] = {}

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, file_path
                FROM lib_tracks
                WHERE codec IS NULL
                  AND file_path IS NOT NULL
                  AND source_platform = 'navidrome'
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
    except Exception as exc:
        logger.error("tag_enrichment: could not query lib_tracks: %s", exc)
        return {"processed": 0, "skipped": 0, "errors": 1}

    if not rows:
        logger.info("tag_enrichment: nothing to enrich")
        return {"processed": 0, "skipped": 0, "errors": 0}

    logger.info("tag_enrichment: processing %d tracks", len(rows))

    batch: list[tuple] = []

    for row in rows:
        if stop_event and stop_event.is_set():
            break

        track_id: str = row["id"]
        rel_path: str = row["file_path"]

        # Resolve absolute path: MUSIC_DIR / relative_file_path
        abs_path = os.path.join(music_dir, rel_path)

        if not os.path.isfile(abs_path):
            missing_dir = os.path.dirname(abs_path) or music_dir
            missing_by_dir[missing_dir] = missing_by_dir.get(missing_dir, 0) + 1
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "tag_enrichment: file not found for track %s: %s", track_id, abs_path
                )
            skipped += 1
            if on_progress:
                on_progress(processed, skipped, errors, len(rows))
            continue

        tags = _extract_tags(abs_path)
        if tags is None:
            errors += 1
            if on_progress:
                on_progress(processed, skipped, errors, len(rows))
            continue

        batch.append((
            tags["bitrate"],
            tags["codec"],
            tags["container"],
            tags["replay_gain_track"],
            tags["embedded_lyrics"],
            tags["tag_genre"],
            track_id,
        ))

        # Commit when batch is full
        if len(batch) >= batch_size:
            _flush_batch(batch)
            processed += len(batch)
            if on_progress:
                on_progress(processed, skipped, errors, len(rows))
            batch = []

    # Flush remainder
    if batch:
        _flush_batch(batch)
        processed += len(batch)
        if on_progress:
            on_progress(processed, skipped, errors, len(rows))

    if missing_by_dir:
        total_missing = sum(missing_by_dir.values())
        sorted_dirs = sorted(
            missing_by_dir.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        top_dirs = sorted_dirs[:5]
        dir_summary = ", ".join(f"{path} ({count})" for path, count in top_dirs)
        extra = ""
        if len(sorted_dirs) > len(top_dirs):
            extra = f" (+{len(sorted_dirs) - len(top_dirs)} more folders)"
        logger.warning(
            "tag_enrichment: %d files missing across %d folders: %s%s (enable DEBUG for per-file paths)",
            total_missing,
            len(missing_by_dir),
            dir_summary,
            extra,
        )

    logger.info(
        "tag_enrichment: processed=%d skipped=%d errors=%d",
        processed, skipped, errors,
    )
    return {"processed": processed, "skipped": skipped, "errors": errors}


def _flush_batch(batch: list[tuple]) -> None:
    """
    Write a batch of (bitrate, codec, container, replay_gain_track,
    embedded_lyrics, tag_genre, track_id) rows to lib_tracks.

    COALESCE guards ensure existing non-NULL values are never overwritten.
    """
    try:
        with _connect() as conn:
            conn.executemany(
                """
                UPDATE lib_tracks
                SET
                    bitrate           = COALESCE(?, bitrate),
                    codec             = COALESCE(?, codec),
                    container         = COALESCE(?, container),
                    replay_gain_track = COALESCE(?, replay_gain_track),
                    embedded_lyrics   = COALESCE(?, embedded_lyrics),
                    tag_genre         = COALESCE(?, tag_genre),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                batch,
            )
    except Exception as exc:
        logger.error("tag_enrichment: batch flush failed: %s", exc)
