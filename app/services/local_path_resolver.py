"""
local_path_resolver.py - Resolve Navidrome relative file paths against real disk folders.

Primary behavior is strict:
  1) exact path under MUSIC_DIR

Fallback behavior (only when exact path is missing):
  2) resolve artist folder
  3) match album folder using normalized names with artist/year prefix stripping
  4) apply original track subpath under the matched album folder

Fallback is accepted only when the best match is high-confidence and unambiguous.
"""
from __future__ import annotations

import difflib
import os
import re
from functools import lru_cache

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_ALBUM_STRICT_SCORE = 0.90
_ARTIST_STRICT_SCORE = 0.93


def _normalize(value: str) -> str:
    text = (value or "").strip().lower().replace("&", " and ")
    text = _NON_ALNUM_RE.sub(" ", text)
    return " ".join(text.split())


def _derive_keys(value: str, *, artist_name: str | None = None) -> set[str]:
    """
    Produce normalized comparison keys including trimmed-prefix variants.
    """
    base = _normalize(value)
    if not base:
        return set()

    artist_norm = _normalize(artist_name or "")
    keys: set[str] = set()
    queue = [base]

    while queue:
        current = queue.pop(0).strip()
        if not current or current in keys:
            continue
        keys.add(current)

        parts = current.split()
        if parts and _YEAR_RE.match(parts[0]):
            stripped_year = " ".join(parts[1:]).strip()
            if stripped_year and stripped_year not in keys:
                queue.append(stripped_year)

        if artist_norm and current.startswith(f"{artist_norm} "):
            stripped_artist = current[len(artist_norm):].strip()
            if stripped_artist and stripped_artist not in keys:
                queue.append(stripped_artist)

    return keys


def _best_name_score(target_keys: set[str], candidate_name: str, *, artist_name: str | None = None) -> float:
    """
    Return a 0..1 confidence score for target vs candidate folder name.
    """
    if not target_keys:
        return 0.0

    candidate_keys = _derive_keys(candidate_name, artist_name=artist_name)
    if not candidate_keys:
        return 0.0

    if target_keys.intersection(candidate_keys):
        return 1.0

    best = 0.0
    for t in target_keys:
        for c in candidate_keys:
            best = max(best, difflib.SequenceMatcher(None, t, c).ratio())
    return best


@lru_cache(maxsize=4096)
def _list_subdirs(parent_dir: str) -> tuple[str, ...]:
    try:
        entries = []
        for name in os.listdir(parent_dir):
            path = os.path.join(parent_dir, name)
            if os.path.isdir(path):
                entries.append(name)
        return tuple(sorted(entries))
    except OSError:
        return tuple()


def _resolve_artist_dir(music_dir: str, artist_segment: str, artist_name: str | None) -> str | None:
    exact_artist_dir = os.path.join(music_dir, artist_segment)
    if os.path.isdir(exact_artist_dir):
        return exact_artist_dir

    target_keys = _derive_keys(artist_name or artist_segment)
    candidates = _list_subdirs(music_dir)
    scored: list[tuple[float, str]] = []
    for candidate in candidates:
        score = _best_name_score(target_keys, candidate)
        if score >= _ARTIST_STRICT_SCORE:
            scored.append((score, candidate))
    if not scored:
        return None

    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None

    return os.path.join(music_dir, scored[0][1])


def _candidate_album_file(
    artist_dir: str,
    album_dir_name: str,
    remainder_parts: list[str],
) -> str | None:
    if not remainder_parts:
        return None
    candidate = os.path.join(artist_dir, album_dir_name, *remainder_parts)
    if os.path.isfile(candidate):
        return candidate
    return None


def resolve_library_file_path(
    music_dir: str,
    rel_path: str,
    *,
    artist_name: str | None = None,
    album_title: str | None = None,
) -> tuple[str | None, str]:
    """
    Resolve a track path under MUSIC_DIR.

    Returns:
      (abs_path, "exact") when exact path exists
      (abs_path, "fallback") when resolved via artist/album fallback
      (None, "missing") when no safe match is found
      (None, "ambiguous") when multiple equal candidates exist
    """
    base_dir = (music_dir or "").rstrip("/\\")
    rel_clean = (rel_path or "").replace("\\", "/").lstrip("/")
    if not base_dir or not rel_clean:
        return None, "missing"

    parts = [p for p in rel_clean.split("/") if p]
    if not parts:
        return None, "missing"

    exact = os.path.normpath(os.path.join(base_dir, *parts))
    if os.path.isfile(exact):
        return exact, "exact"

    if len(parts) < 3:
        return None, "missing"

    artist_segment = parts[0]
    album_segment = parts[1]
    remainder_parts = parts[2:]

    artist_dir = _resolve_artist_dir(base_dir, artist_segment, artist_name)
    if not artist_dir:
        return None, "missing"

    target_album_keys = _derive_keys(album_title or album_segment, artist_name=artist_name)
    if not target_album_keys:
        target_album_keys = _derive_keys(album_segment, artist_name=artist_name)

    scored_files: list[tuple[float, str]] = []
    for album_dir_name in _list_subdirs(artist_dir):
        score = _best_name_score(target_album_keys, album_dir_name, artist_name=artist_name)
        if score < _ALBUM_STRICT_SCORE:
            continue
        candidate_file = _candidate_album_file(artist_dir, album_dir_name, remainder_parts)
        if candidate_file:
            scored_files.append((score, candidate_file))

    if not scored_files:
        return None, "missing"

    scored_files.sort(reverse=True)
    if len(scored_files) > 1 and scored_files[0][0] == scored_files[1][0]:
        return None, "ambiguous"

    return scored_files[0][1], "fallback"
