"""Database restore script — restore from backup files.

Replaces ``db_restore.sh``.  Uses :mod:`subprocess` to invoke
``pg_restore`` with safety checks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import check_db_connection
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert

log = get_logger("db_restore")

BACKUP_DIR = Path("/opt/acmecorp/backups/database")


def _find_latest_backup(backup_type: str = "full") -> Path | None:
    """Find the most recent backup directory."""
    base = BACKUP_DIR / backup_type
    if not base.is_dir():
        return None

    dirs = sorted(
        (d for d in base.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    return dirs[0] if dirs else None


def restore_database(
    config: PipelineConfig,
    backup_path: Path | None = None,
    profile: str = "production",
    tables: list[str] | None = None,
) -> bool:
    """Restore the database from a backup.

    Args:
        config: Pipeline configuration.
        backup_path: Path to a specific backup directory.  If ``None``,
            the latest full backup is used.
        profile: Database profile name.
        tables: Optional list of specific tables to restore.

    Returns:
        ``True`` on success.
    """
    db = config.db_profiles.get(profile)
    if db is None:
        log.error("Unknown DB profile: %s", profile)
        return False

    if not check_db_connection(config, profile):
        log.error("Cannot connect to database for restore")
        return False

    if backup_path is None:
        backup_path = _find_latest_backup("full")
        if backup_path is None:
            log.error("No backup found to restore from")
            return False

    log.info("Restoring from: %s", backup_path)

    with Lock("db_restore", timeout=30):
        env = {**os.environ, "PGPASSWORD": db.password}

        dump_files = list(backup_path.glob("*.sql.gz")) + list(backup_path.glob("*.dump"))
        if not dump_files:
            log.error("No dump files found in %s", backup_path)
            return False

        success = True
        for dump_file in dump_files:
            log.info("Restoring: %s", dump_file.name)
            cmd = [
                "pg_restore",
                "-h", db.host,
                "-p", str(db.port),
                "-U", db.user,
                "-d", db.dbname,
                "--verbose",
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
            ]

            if tables:
                for t in tables:
                    cmd.extend(["-t", t])

            cmd.append(str(dump_file))

            log_file = backup_path / "restore.log"
            try:
                with open(log_file, "a") as err:
                    result = subprocess.run(cmd, capture_output=False, stderr=err, env=env)
                if result.returncode != 0:
                    log.error("pg_restore failed for %s (exit %d)", dump_file.name, result.returncode)
                    success = False
            except OSError as exc:
                log.error("pg_restore error: %s", exc)
                success = False

        if success:
            log.info("Database restore completed successfully")
        else:
            log.error("Database restore completed with errors")
            alert("Database restore completed with errors — check logs", "WARNING", config)

        return success


def main() -> None:
    """CLI entry point matching original ``db_restore.sh`` interface."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    backup_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    ok = restore_database(cfg, backup_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
