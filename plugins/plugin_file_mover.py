"""
plugin_file_mover.py - Universal file_handler plugin for Rythmx.

This plugin copies downloaded files into the music library and normalizes
destination permissions so library services can read and write them.
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)


class UniversalFileMover:
    """
    Copies downloaded audio files into FILE_MOVER_DEST/{Artist}/{Album}/.
    """

    name = "file_mover"

    def __init__(self) -> None:
        self._dest = (os.environ.get("FILE_MOVER_DEST") or "").rstrip("/")
        self._dir_mode = _parse_octal_mode(os.environ.get("FILE_MOVER_DIR_MODE"), default=0o775)
        self._file_mode = _parse_octal_mode(os.environ.get("FILE_MOVER_FILE_MODE"), default=0o664)

    def organize(self, artifact) -> object:
        if not self._dest:
            logger.warning("file_mover: FILE_MOVER_DEST not configured - skipping organize")
            return artifact

        if not artifact.files:
            logger.warning("file_mover: artifact has no files - skipping organize")
            return artifact

        dest_dir = os.path.join(
            self._dest,
            _safe_name(artifact.artist or "Unknown Artist"),
            _safe_name(artifact.album or "Unknown Album"),
        )
        os.makedirs(dest_dir, exist_ok=True)
        _safe_chmod(dest_dir, self._dir_mode)

        copied = 0
        for src in sorted(artifact.files):
            dst = os.path.join(dest_dir, os.path.basename(src))
            shutil.copy2(src, dst)
            _safe_chmod(dst, self._file_mode)
            logger.debug("file_mover: copied %s into %s", os.path.basename(src), dest_dir)
            copied += 1

        logger.info("file_mover: copied %d file(s) to %s", copied, dest_dir)
        artifact.dest_dir = dest_dir
        return artifact


def _safe_name(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch not in r'\/:*?"<>|').strip()


def _parse_octal_mode(raw: str | None, *, default: int) -> int:
    value = str(raw or "").strip()
    if not value:
        return default
    try:
        parsed = int(value, 8)
    except ValueError:
        logger.warning(
            "file_mover: invalid mode '%s' (expected octal like 775/664); using %s",
            value,
            oct(default),
        )
        return default
    if parsed < 0:
        return default
    return parsed


def _safe_chmod(path: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        logger.warning("file_mover: chmod failed for %s to %s: %s", path, oct(mode), exc)


PLUGIN_API_VERSION = 2
PLUGIN_VERSION = "1.1.0"
PLUGIN_DESCRIPTION = "Copies downloaded audio files into your music library."
CAPABILITIES = {
    "fetch_contract_version": 1,
    # NO pre_fetch_enrichment_version — this plugin doesn't participate in enrichment
    "roles": ["file_handler"],
    "error_taxonomy": ["recoverable", "permanent", "config"],
}

PLUGIN_SLOTS = {
    "file_handler": UniversalFileMover,
}

CONFIG_SCHEMA = [
    {
        "key": "FILE_MOVER_DEST",
        "label": "Music library destination path",
        "type": "text",
        "required": True,
        "placeholder": "/music",
    },
    {
        "key": "FILE_MOVER_DIR_MODE",
        "label": "Directory permissions (octal)",
        "type": "text",
        "required": False,
        "default": "775",
        "placeholder": "775",
    },
    {
        "key": "FILE_MOVER_FILE_MODE",
        "label": "File permissions (octal)",
        "type": "text",
        "required": False,
        "default": "664",
        "placeholder": "664",
    },
]
