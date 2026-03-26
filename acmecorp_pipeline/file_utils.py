"""File manipulation utilities for the AcmeCorp data pipeline.

Replaces ``file_utils.sh``.  Uses :mod:`pathlib`, :mod:`csv`, and :mod:`json`
instead of shell commands like ``wc``, ``head``, ``awk``, and ``stat``.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("file_utils")


def check_file(filepath: Path) -> bool:
    """Return ``True`` if *filepath* exists and is non-empty.

    Logs a warning/error and returns ``False`` otherwise.
    """
    if not filepath.is_file():
        log.error("File not found: %s", filepath)
        return False
    if filepath.stat().st_size == 0:
        log.warning("File is empty: %s", filepath)
        return False
    return True


def count_data_rows(filepath: Path, has_header: bool = True) -> int:
    """Count data rows in a file, optionally excluding the header line."""
    try:
        with open(filepath) as fh:
            total = sum(1 for _ in fh)
        return max(total - 1, 0) if has_header else total
    except OSError as exc:
        log.error("Cannot count rows in %s: %s", filepath, exc)
        return 0


def archive_file(
    filepath: Path,
    archive_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Create a timestamped archive copy of *filepath*.

    Returns the archive path on success, ``None`` on failure.
    """
    if archive_dir is None:
        archive_dir = Path("/opt/acmecorp/data/archive")
    archive_dir.mkdir(parents=True, exist_ok=True)

    datestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = filepath.stem
    suffix = filepath.suffix
    archive_path = archive_dir / f"{stem}_{datestamp}{suffix}"

    try:
        shutil.copy2(str(filepath), str(archive_path))
        log.info("Archived: %s -> %s", filepath, archive_path)
        return archive_path
    except OSError as exc:
        log.error("Failed to archive %s: %s", filepath, exc)
        return None


def move_file(src: Path, dest: Path, retries: int = 3) -> bool:
    """Move a file with retry logic.  Returns ``True`` on success."""
    for attempt in range(1, retries + 1):
        try:
            dest_path = dest / src.name if dest.is_dir() else dest
            shutil.move(str(src), str(dest_path))
            log.info("Moved: %s -> %s", src, dest_path)
            return True
        except OSError as exc:
            log.warning("Move failed (attempt %d/%d): %s -> %s: %s", attempt, retries, src, dest, exc)
            if attempt < retries:
                time.sleep(2)
    log.error("Failed to move file after %d attempts: %s", retries, src)
    return False


def validate_csv(
    filepath: Path,
    expected_cols: Optional[int] = None,
    delimiter: str = ",",
) -> bool:
    """Validate CSV structure.

    Checks:
    - File exists and is non-empty
    - Optional column-count match
    - Consistent column count across the first 100 rows

    Returns ``True`` if valid.
    """
    if not check_file(filepath):
        return False

    try:
        with open(filepath, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            header = next(reader, None)
            if header is None:
                log.error("CSV file has no header: %s", filepath)
                return False

            header_cols = len(header)
            if expected_cols is not None and header_cols != expected_cols:
                log.error(
                    "CSV column mismatch: expected %d, got %d in %s",
                    expected_cols, header_cols, filepath,
                )
                return False

            inconsistent_lines: list[int] = []
            for i, row in enumerate(reader, start=2):
                if i > 100:
                    break
                if len(row) != header_cols:
                    inconsistent_lines.append(i)

            if inconsistent_lines:
                log.warning("Inconsistent column counts at lines: %s", inconsistent_lines)
                return False

        log.info("CSV validation passed: %s (%d columns)", filepath, header_cols)
        return True
    except (csv.Error, OSError) as exc:
        log.error("CSV validation error for %s: %s", filepath, exc)
        return False


def validate_json(filepath: Path) -> bool:
    """Validate that *filepath* contains well-formed JSON.

    Returns ``True`` if valid.
    """
    if not check_file(filepath):
        return False

    try:
        with open(filepath) as fh:
            json.load(fh)
        log.info("JSON validation passed: %s", filepath)
        return True
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Invalid JSON %s: %s", filepath, exc)
        return False


def file_size_human(filepath: Path) -> str:
    """Return file size in a human-readable string (e.g. ``1.2 MB``)."""
    if not filepath.is_file():
        return "0"
    size = filepath.stat().st_size
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def wait_for_file(
    filepath: Path,
    timeout: int = 300,
    interval: int = 10,
) -> bool:
    """Block until *filepath* appears and finishes writing.

    Returns ``True`` if the file became available within the timeout.
    """
    log.info("Waiting for file: %s (timeout: %ds)", filepath, timeout)
    elapsed = 0

    while not filepath.is_file():
        time.sleep(interval)
        elapsed += interval
        if elapsed >= timeout:
            log.error("Timeout waiting for file: %s", filepath)
            return False

    # Wait for size to stabilise (file finished writing)
    prev_size = -1
    while True:
        cur_size = filepath.stat().st_size
        if cur_size == prev_size:
            break
        prev_size = cur_size
        time.sleep(2)

    log.info("File ready: %s (%s)", filepath, file_size_human(filepath))
    return True
