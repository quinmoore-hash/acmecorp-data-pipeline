"""Database backup script — daily incremental + weekly full backups.

Replaces ``db_backup.sh``.  Uses :mod:`subprocess` to invoke
``pg_dump`` and :mod:`boto3` for S3 uploads.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import run_query
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert
from acmecorp_pipeline.s3_sync import s3_upload_dir

log = get_logger("db_backup")

BACKUP_DIR = Path("/opt/acmecorp/backups/database")
RETENTION_DAYS = 30
FULL_RETENTION_DAYS = 90


def _pg_dump_table(
    config: PipelineConfig,
    table: str,
    output_file: Path,
    log_file: Path,
    profile: str = "production",
) -> bool:
    """Dump a single table to a gzipped file.  Returns ``True`` on success."""
    db = config.db_profiles.get(profile)
    if db is None:
        log.error("Unknown DB profile: %s", profile)
        return False

    cmd = [
        "pg_dump",
        "-h", db.host,
        "-p", str(db.port),
        "-U", db.user,
        "-d", db.dbname,
        "-t", table,
        "--format=custom",
        "--compress=9",
    ]
    env = {"PGPASSWORD": db.password}

    try:
        with open(output_file, "wb") as out, open(log_file, "a") as err:
            result = subprocess.run(cmd, stdout=out, stderr=err, env={**os.environ, **env})
        return result.returncode == 0
    except OSError as exc:
        log.error("pg_dump failed for %s: %s", table, exc)
        return False


def _pg_dump_full(
    config: PipelineConfig,
    output_file: Path,
    log_file: Path,
    profile: str = "production",
) -> bool:
    """Full database dump.  Returns ``True`` on success."""
    db = config.db_profiles.get(profile)
    if db is None:
        log.error("Unknown DB profile: %s", profile)
        return False

    cmd = [
        "pg_dump",
        "-h", db.host,
        "-p", str(db.port),
        "-U", db.user,
        "-d", db.dbname,
        "--verbose",
        "--format=custom",
        "--compress=9",
    ]
    env = {"PGPASSWORD": db.password}

    try:
        with open(output_file, "wb") as out, open(log_file, "a") as err:
            result = subprocess.run(cmd, stdout=out, stderr=err, env={**os.environ, **env})
        return result.returncode == 0
    except OSError as exc:
        log.error("Full pg_dump failed: %s", exc)
        return False


def _cleanup_old_backups(base_dir: Path, retention_days: int) -> None:
    """Remove backup directories older than *retention_days*."""
    import time as _time
    cutoff = _time.time() - retention_days * 86400
    if not base_dir.is_dir():
        return
    for entry in base_dir.iterdir():
        if entry.is_dir():
            try:
                if entry.stat().st_mtime < cutoff:
                    import shutil
                    shutil.rmtree(entry)
            except OSError:
                pass


def run_backup(config: PipelineConfig, full: bool = False) -> bool:
    """Execute a database backup.

    Args:
        config: Pipeline configuration.
        full: If ``True``, perform a full backup; otherwise incremental.

    Returns:
        ``True`` on success.
    """
    backup_type = "full" if full else "incremental"

    with Lock("db_backup", timeout=60):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_subdir = BACKUP_DIR / backup_type / timestamp
        backup_subdir.mkdir(parents=True, exist_ok=True)
        log_file = backup_subdir / "backup.log"

        log.info("Starting %s database backup...", backup_type)
        start_time = time.time()

        if full:
            dump_file = backup_subdir / f"warehouse_full_{timestamp}.sql.gz"
            success = _pg_dump_full(config, dump_file, log_file)
        else:
            # Incremental: only dump tables modified today
            rows = run_query(
                config,
                """SELECT schemaname || '.' || tablename
                   FROM pg_stat_user_tables
                   WHERE last_analyze >= CURRENT_DATE
                      OR last_autoanalyze >= CURRENT_DATE;""",
                profile="production",
            )
            tables = [r[0] for r in rows] if rows else []

            if not tables:
                log.info("No tables modified today, skipping incremental backup")
                import shutil
                shutil.rmtree(backup_subdir, ignore_errors=True)
                return True

            success = True
            for table in tables:
                safe_name = table.replace(".", "_")
                dump_file = backup_subdir / f"{safe_name}_{timestamp}.sql.gz"
                log.info("Backing up table: %s", table)
                if not _pg_dump_table(config, table, dump_file, log_file):
                    log.error("Failed to backup table: %s", table)
                    success = False

        duration = int(time.time() - start_time)

        if success:
            # Calculate backup size
            total_size = sum(f.stat().st_size for f in backup_subdir.iterdir() if f.is_file())
            size_mb = total_size / (1024 * 1024)
            log.info("Backup completed: %s (%.1f MB, %ds)", backup_type, size_mb, duration)

            # Upload to S3
            s3_prefix = f"backups/database/{backup_type}/{timestamp}/"
            if not s3_upload_dir(backup_subdir, config, s3_prefix):
                log.warning("Failed to upload backup to S3")
                alert("DB backup S3 upload failed (local backup OK)", "WARNING", config)

            # Write manifest
            manifest = backup_subdir / "manifest.txt"
            manifest.write_text(
                f"type={backup_type}\n"
                f"timestamp={timestamp}\n"
                f"size={size_mb:.1f}MB\n"
                f"duration={duration}\n"
                f"host={config.db_profiles['production'].host}\n"
                f"database={config.db_profiles['production'].dbname}\n"
            )
        else:
            log.error("Backup FAILED: %s", backup_type)
            alert(f"Database {backup_type} backup FAILED! Duration: {duration}s", "CRITICAL", config)
            return False

        # Cleanup old backups
        log.info("Cleaning up old backups...")
        _cleanup_old_backups(BACKUP_DIR / "incremental", RETENTION_DAYS)
        _cleanup_old_backups(BACKUP_DIR / "full", FULL_RETENTION_DAYS)

        log.info("Backup job complete")
        return True


def main() -> None:
    """CLI entry point matching original ``db_backup.sh`` interface."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    full = "--full" in sys.argv
    ok = run_backup(cfg, full=full)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
