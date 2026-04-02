"""
Shared models and utilities for music catalog clients.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ARTICLES = frozenset({"the", "a", "an"})
MB_USER_AGENT = "rythmx/1.0 (https://github.com/snuffomega/rythmx)"


def norm(s: str) -> str:
    """
    Normalize a string for cross-service artist/album matching.
    NFKC unicode + lowercase + strip leading articles + remove punctuation.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    words = s.split()
    if words and words[0] in ARTICLES:
        words = words[1:]
    s = " ".join(words)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class Release:
    artist: str
    title: str
    release_date: str
    kind: str
    source: str
    source_url: str = ""
    deezer_album_id: str = ""
    spotify_album_id: str = ""
    itunes_album_id: str = ""
    artwork_url: str = ""
    is_upcoming: bool = False

