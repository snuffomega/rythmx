# app/core/logging.py
from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from core.config import CONFIG


def _ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)


def setup_logger(script_name: str, level: int = logging.INFO) -> tuple[logging.Logger, str]:
    """
    Standard logger for runners:
    - Console logging
    - Per-run log file in logs_dir
    - Adds run_id and script fields
    """
    _ensure_dir(CONFIG["logs_dir"])

    run_id = f"{script_name}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    log_path = Path(CONFIG["logs_dir"]) / f"{script_name}.log"

    logger = logging.getLogger(script_name)
    logger.setLevel(level)
    logger.propagate = False

    # Clear handlers if reloaded / re-run inside same interpreter
    if logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # File handler
    fh = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    # Also set root logger level if nothing configured (keeps 3rd-party libs from being silent)
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)
        root.addHandler(ch)

    logger.info(f"Starting {script_name} run_id={run_id}")
    logger.debug(f"env_file={CONFIG['env_file']} logs_dir={CONFIG['logs_dir']} state_dir={CONFIG['state_dir']}")
    return logger, run_id
