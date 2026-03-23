from __future__ import annotations

"""
Logging configuration for the filesync daemon.
RotatingFileHandler: 10MB per file, 5 rotations.
Writes to /Users/Shared/CelesteOS/logs/filesync.log + stdout for launchd capture.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("/Users/Shared/CelesteOS/logs")
LOG_FILE = LOG_DIR / "filesync.log"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """
    Set up logging with both file rotation and stdout output.
    Call this once at daemon startup, before any logging happens.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (from basicConfig)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    # Stdout handler (for launchd capture and foreground mode)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(level)
    root.addHandler(stdout_handler)

    # File handler with rotation
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        # Can't write to log directory — log to stdout only
        root.warning("Cannot set up file logging at %s: %s", LOG_FILE, exc)
