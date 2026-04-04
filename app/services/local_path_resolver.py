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
_TRACK_PREFIX_RE = re.compile(r"^\d{1,3}(?:[-_. ]\d{1,3})?\s*[-_. ]+\s*")

_ALBUM_STRICT_SCORE = 0.90
_ARTIST_STRICT_SCORE = 0.93
_TRACK_STRICT_SCORE = 0.90
_AMBIGUOUS_EPSILON = 0.01


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


@lru_cache(maxsize=4096)
def _list_files_recursive(root_dir: str, max_depth: int = 2) -> tuple[str, ...]:
    """
    Return relative file paths under root_dir up to max_depth.
    """
    try:
        files: list[str] = []
        for walk_root, dirs, filenames in os.walk(root_dir):
            rel_root = os.path.relpath(walk_root, root_dir)
            depth = 0 if rel_root in (".", "") else rel_root.count(os.sep) + 1
            if depth > max_depth:
                dirs[:] = []
                continue

            for filename in filenames:
                if rel_root in (".", ""):
                    files.append(filename)
                else:
                    files.append(os.path.join(rel_root, filename))
        return tuple(sorted(files))
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


def _strip_track_prefix(stem: str) -> str:
    return _TRACK_PREFIX_RE.sub("", stem or "").strip()


def _track_name_score(expected_filename: str, candidate_filename: str) -> float:
    exp_stem, exp_ext = os.path.splitext(expected_filename)
    cand_stem, cand_ext = os.path.splitext(candidate_filename)

    if exp_ext and cand_ext and exp_ext.lower() != cand_ext.lower():
        return 0.0

    exp_norm = _normalize(exp_stem)
    cand_norm = _normalize(cand_stem)
    if not exp_norm or not cand_norm:
        return 0.0

    if exp_norm == cand_norm:
        return 1.0

    exp_title = _strip_track_prefix(exp_norm)
    cand_title = _strip_track_prefix(cand_norm)
    if exp_title and cand_title and exp_title == cand_title:
        return 1.0

    full_score = difflib.SequenceMatcher(None, exp_norm, cand_norm).ratio()
    title_score = 0.0
    if exp_title and cand_title:
        title_score = difflib.SequenceMatcher(None, exp_title, cand_title).ratio()

    return max(full_score * 0.85, title_score)


def _find_track_file_fallback(album_dir: str, remainder_parts: list[str]) -> tuple[str | None, float]:
    """
    Attempt a safe filename fallback inside a matched album folder.
    """
    if not remainder_parts:
        return None, 0.0

    expected_filename = remainder_parts[-1]
    expected_ext = os.path.splitext(expected_filename)[1].lower()
    candidates = _list_files_recursive(album_dir, max_depth=2)
    if not candidates:
        return None, 0.0

    scored: list[tuple[float, str]] = []
    for rel_file in candidates:
        candidate_filename = os.path.basename(rel_file)
        candidate_ext = os.path.splitext(candidate_filename)[1].lower()
        if expected_ext and candidate_ext and expected_ext != candidate_ext:
            continue

        score = _track_name_score(expected_filename, candidate_filename)
        if score >= _TRACK_STRICT_SCORE:
            scored.append((score, os.path.normpath(os.path.join(album_dir, rel_file))))

    if not scored:
        return None, 0.0

    scored.sort(reverse=True)
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) <= _AMBIGUOUS_EPSILON:
        return None, 0.0

    return scored[0][1], scored[0][0]


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
        album_score = _best_name_score(target_album_keys, album_dir_name, artist_name=artist_name)
        if album_score < _ALBUM_STRICT_SCORE:
            continue
        album_dir = os.path.join(artist_dir, album_dir_name)

        # First preference: exact subpath match inside selected album folder.
        exact_in_album = os.path.normpath(os.path.join(album_dir, *remainder_parts))
        if os.path.isfile(exact_in_album):
            scored_files.append((album_score + 1.0, exact_in_album))
            continue

        # Second preference: filename fallback inside selected album folder.
        candidate_file, track_score = _find_track_file_fallback(album_dir, remainder_parts)
        if candidate_file:
            combined = (album_score * 0.6) + (track_score * 0.4)
            scored_files.append((combined, candidate_file))

    if not scored_files:
        return None, "missing"

    scored_files.sort(reverse=True)
    if len(scored_files) > 1 and abs(scored_files[0][0] - scored_files[1][0]) <= _AMBIGUOUS_EPSILON:
        return None, "ambiguous"

    return scored_files[0][1], "fallback"
