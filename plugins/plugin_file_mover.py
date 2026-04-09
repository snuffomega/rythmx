"""
plugin_file_mover.py — Universal file_handler plugin for Rythmx.

Fills the ``file_handler`` slot.  Works with any downloader that produces a
DownloadArtifact — it has zero knowledge of Tidarr, SABnzbd, or any other
download tool.

Config (set as env vars on the Rythmx container):

    FILE_MOVER_DEST   Root of your music library inside the Rythmx container.
                      Must be writable.  Example: /music

Flow:
    Core already called translate_path() on the downloader and populated
    artifact.files with the FLAC paths visible to Rythmx.  This plugin only
    needs to copy those files into the library tree.
"""

from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin implementation
# ---------------------------------------------------------------------------

class UniversalFileMover:
    """
    Copies downloaded audio files into the music library tree.

    Destination: FILE_MOVER_DEST/{Artist}/{Album}/

    Sets ``artifact.dest_dir`` to the destination directory and returns the
    mutated artifact so subsequent pipeline steps (e.g. a tagger or the
    library-confirm step) know where the files landed.
    """

    name = "file_mover"

    def __init__(self) -> None:
        self._dest = (os.environ.get("FILE_MOVER_DEST") or "").rstrip("/")

    # ------------------------------------------------------------------
    # FileHandlerPlugin protocol
    # ------------------------------------------------------------------

    def organize(self, artifact) -> object:
        """
        Copy artifact.files into the music library.

        Args:
            artifact: DownloadArtifact — fields used:
                        .artist   str
                        .album    str
                        .files    list[str]   absolute paths visible to Rythmx

        Returns:
            The artifact with .dest_dir set to the library destination
            directory, or unchanged if the plugin is not configured or there
            are no files to move.
        """
        if not self._dest:
            logger.warning(
                "file_mover: FILE_MOVER_DEST not configured — skipping organize"
            )
            return artifact

        if not artifact.files:
            logger.warning("file_mover: artifact has no files — skipping organize")
            return artifact

        dest_dir = os.path.join(
            self._dest,
            _safe_name(artifact.artist or "Unknown Artist"),
            _safe_name(artifact.album or "Unknown Album"),
        )
        os.makedirs(dest_dir, exist_ok=True)

        copied = 0
        for src in sorted(artifact.files):
            dst = os.path.join(dest_dir, os.path.basename(src))
            shutil.copy2(src, dst)
            logger.debug("file_mover: %s → %s", os.path.basename(src), dest_dir)
            copied += 1

        logger.info(
            "file_mover: copied %d file(s) to %s", copied, dest_dir
        )
        artifact.dest_dir = dest_dir
        return artifact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    """Strip characters that are illegal in file-system path components."""
    return "".join(c for c in s if c not in r'\/:*?"<>|').strip()


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

PLUGIN_API_VERSION = 2
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Copies downloaded audio files into your music library."
CAPABILITIES = {
    "fetch_contract_version": 1,
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
]
