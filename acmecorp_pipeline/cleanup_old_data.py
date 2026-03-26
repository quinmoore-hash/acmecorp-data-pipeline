"""Disk cleanup — removes old data files, logs, and temporary artifacts.

Replaces ``cleanup_old_data.sh``.  Runs daily at 6 AM via cron.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert

log = get_logger("cleanup_old_data")


def _remove_old_files(directory: Path, max_age_days: int, pattern: str = "*") -> int:
    """Remove files older than *max_age_days* matching *pattern*.

    Returns the number of files removed.
    """
    if not directory.is_dir():
        return 0

    cutoff = time.time() - max_age_days * 86400
    removed = 0

    for filepath in directory.glob(pattern):
        if not filepath.is_file():
            continue
        try:
            if filepath.stat().st_mtime < cutoff:
                filepath.unlink()
                removed += 1
        except OSError as exc:
            log.warning("Failed to remove %s: %s", filepath, exc)

    return removed


def _remove_old_dirs(directory: Path, max_age_days: int) -> int:
    """Remove sub-directories older than *max_age_days*.

    Returns the number of directories removed.
    """
    if not directory.is_dir():
        return 0

    cutoff = time.time() - max_age_days * 86400
    removed = 0

    for entry in directory.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry)
                removed += 1
        except OSError as exc:
            log.warning("Failed to remove directory %s: %s", entry, exc)

    return removed


def run_cleanup(config: PipelineConfig) -> None:
    """Execute the disk cleanup routine."""
    log.info("Starting disk cleanup...")

    total_removed = 0

    # 1. Archive directory — remove files older than archive_retention_days
    archive_days = config.processing.archive_retention_days
    n = _remove_old_files(config.paths.archive_dir, archive_days)
    log.info("Archived data: removed %d files older than %d days", n, archive_days)
    total_removed += n

    # 2. Processed directory — remove files older than 7 days
    n = _remove_old_files(config.paths.processed_dir, 7)
    log.info("Processed data: removed %d files older than 7 days", n)
    total_removed += n

    # 3. Staging directory — remove all files older than 1 day
    n = _remove_old_files(config.paths.staging_dir, 1)
    log.info("Staging data: removed %d files older than 1 day", n)
    total_removed += n

    # 4. Error directory — remove files older than 30 days
    n = _remove_old_files(config.paths.error_dir, 30)
    log.info("Error files: removed %d files older than 30 days", n)
    total_removed += n

    # 5. Log files — remove logs older than log_retention_days
    log_days = config.log_cfg.retention_days
    n = _remove_old_files(config.paths.log_dir, log_days, "*.log")
    log.info("Log files: removed %d files older than %d days", n, log_days)
    total_removed += n

    # 6. Rotated log files — remove .gz logs older than 2x retention
    n = _remove_old_files(config.paths.log_dir, log_days * 2, "*.gz")
    log.info("Rotated logs: removed %d files older than %d days", n, log_days * 2)
    total_removed += n

    # 7. Temp files — remove stale tmp files
    tmp_dirs = [Path("/tmp")]
    for tmp_dir in tmp_dirs:
        n = _remove_old_files(tmp_dir, 3, "acmecorp_*")
        log.info("Temp files in %s: removed %d files older than 3 days", tmp_dir, n)
        total_removed += n

    # 8. Old backup directories (local)
    backup_base = Path("/opt/acmecorp/backups/database")
    n = _remove_old_dirs(backup_base / "incremental", 30)
    log.info("Incremental backups: removed %d dirs older than 30 days", n)
    total_removed += n

    n = _remove_old_dirs(backup_base / "full", 90)
    log.info("Full backups: removed %d dirs older than 90 days", n)
    total_removed += n

    # Check disk usage after cleanup
    try:
        usage = shutil.disk_usage(str(config.paths.base_dir))
        pct_used = int(usage.used * 100 / usage.total)
        free_gb = usage.free / (1024**3)
        log.info("Disk after cleanup: %d%% used, %.1f GB free", pct_used, free_gb)

        if pct_used > 90:
            alert(f"Disk still at {pct_used}% after cleanup ({free_gb:.1f} GB free)", "WARNING", config)
    except OSError:
        pass

    log.info("Cleanup complete: removed %d items total", total_removed)


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)
    run_cleanup(cfg)


if __name__ == "__main__":
    main()
