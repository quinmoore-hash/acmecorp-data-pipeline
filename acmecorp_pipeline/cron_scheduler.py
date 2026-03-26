"""Cron job installer / manager for the AcmeCorp data pipeline.

Replaces ``cron_scheduler.sh``.  Uses :mod:`subprocess` to manage the
crontab since there is no pure-Python cron API on most systems.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("cron_scheduler")

CRON_TAG = "# ACMECORP_PIPELINE"


def _get_script_dir() -> Path:
    """Return the directory containing the Python entry-point scripts."""
    return Path(__file__).resolve().parent


def _current_crontab() -> str:
    """Return the current crontab contents (empty string if none)."""
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _set_crontab(contents: str) -> bool:
    """Replace the current crontab with *contents*.  Returns ``True`` on success."""
    result = subprocess.run(
        ["crontab", "-"],
        input=contents, capture_output=True, text=True,
    )
    return result.returncode == 0


def install_crons() -> None:
    """Install all pipeline cron jobs (replaces existing pipeline entries)."""
    print("Installing pipeline cron jobs...")

    # Remove existing pipeline entries
    existing = _current_crontab()
    cleaned = "\n".join(
        line for line in existing.splitlines()
        if CRON_TAG not in line
    )

    python = sys.executable

    new_entries = f"""
# ==========================================
# AcmeCorp Data Pipeline Scheduled Jobs
# Installed: {datetime.now():%Y-%m-%d %H:%M:%S}
# DO NOT EDIT MANUALLY - use cron_scheduler.py
# ==========================================

# Nightly ETL pipeline - 2:00 AM EST
0 2 * * * {python} -m acmecorp_pipeline.etl_master >> /var/log/acmecorp/pipeline/etl_master.log 2>&1 {CRON_TAG}

# Hourly incremental sync
0 * * * * {python} -m acmecorp_pipeline.incremental_sync >> /var/log/acmecorp/pipeline/incremental_sync.log 2>&1 {CRON_TAG}

# Log parser and alerting - every 15 min
*/15 * * * * {python} -m acmecorp_pipeline.log_monitor >> /var/log/acmecorp/pipeline/log_monitor.log 2>&1 {CRON_TAG}

# Database backup - 1:00 AM EST daily
0 1 * * * {python} -m acmecorp_pipeline.db_backup >> /var/log/acmecorp/pipeline/db_backup.log 2>&1 {CRON_TAG}

# Weekly full backup - Sunday 3:00 AM
0 3 * * 0 {python} -m acmecorp_pipeline.db_backup --full >> /var/log/acmecorp/pipeline/db_backup.log 2>&1 {CRON_TAG}

# Disk cleanup - daily at 6:00 AM
0 6 * * * {python} -m acmecorp_pipeline.cleanup_old_data >> /var/log/acmecorp/pipeline/cleanup.log 2>&1 {CRON_TAG}

# Health check - every 5 minutes
*/5 * * * * {python} -m acmecorp_pipeline.health_check >> /var/log/acmecorp/pipeline/health_check.log 2>&1 {CRON_TAG}

# Monthly report generation - 1st of month at 8:00 AM
0 8 1 * * {python} -m acmecorp_pipeline.generate_report >> /var/log/acmecorp/pipeline/reports.log 2>&1 {CRON_TAG}
"""

    full_crontab = cleaned.rstrip() + "\n" + new_entries
    if _set_crontab(full_crontab):
        print("Cron jobs installed successfully.")
        print(f"Run '{sys.argv[0]} status' to verify.")
    else:
        print("ERROR: Failed to install cron jobs", file=sys.stderr)
        sys.exit(1)


def remove_crons() -> None:
    """Remove all pipeline cron entries."""
    print("Removing pipeline cron jobs...")
    existing = _current_crontab()
    cleaned = "\n".join(
        line for line in existing.splitlines()
        if CRON_TAG not in line
    )
    _set_crontab(cleaned)
    print("Pipeline cron jobs removed.")


def show_status() -> None:
    """Display current pipeline cron entries."""
    print("Current pipeline cron entries:")
    print("================================")
    existing = _current_crontab()
    pipeline_lines = [line for line in existing.splitlines() if CRON_TAG in line]
    for line in pipeline_lines:
        print(line)
    print("================================")
    print(f"Total: {len(pipeline_lines)} jobs")


def main() -> None:
    """CLI entry point matching original ``cron_scheduler.sh`` interface."""
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    actions = {
        "install": install_crons,
        "remove": remove_crons,
        "status": show_status,
    }

    if action not in actions:
        print(f"Usage: {sys.argv[0]} {{install|remove|status}}")
        sys.exit(1)

    actions[action]()


if __name__ == "__main__":
    main()
