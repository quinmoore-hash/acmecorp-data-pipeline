"""Structured logging for the AcmeCorp data pipeline.

Replaces ``logging.sh``.  Provides a pre-configured logger that writes to
both a daily rotating file and stderr, matching the original format:

    [YYYY-MM-DD HH:MM:SS] [LEVEL] [caller] message
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(
    log_dir: Optional[Path] = None,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """Configure and return the pipeline root logger.

    Calling this multiple times is safe; handlers are only added once.
    """
    global _configured

    logger = logging.getLogger("acmecorp_pipeline")

    if _configured:
        return logger

    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Stderr handler (always)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    # File handler (if log_dir is writable)
    if log_dir is None:
        log_dir = Path(os.environ.get("LOG_DIR", "/var/log/acmecorp/pipeline"))

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        if log_file is None:
            log_file = f"pipeline_{datetime.now():%Y%m%d}.log"
        file_path = log_dir / log_file
        file_handler = logging.FileHandler(str(file_path))
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Cannot write to log directory %s; file logging disabled", log_dir)

    _configured = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child logger under the pipeline namespace."""
    base = "acmecorp_pipeline"
    if name:
        return logging.getLogger(f"{base}.{name}")
    return logging.getLogger(base)


def rotate_logs(log_dir: Path, retention_days: int = 90) -> int:
    """Delete log files older than *retention_days*.  Returns count of removed files."""
    import time

    cutoff = time.time() - retention_days * 86400
    removed = 0
    if not log_dir.is_dir():
        return removed
    for log_file in log_dir.glob("*.log"):
        try:
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                removed += 1
        except OSError:
            pass
    return removed
