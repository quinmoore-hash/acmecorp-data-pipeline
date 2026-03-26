"""Incremental data sync — hourly lightweight sync for mid-day data drops.

Replaces ``incremental_sync.sh``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.file_utils import archive_file
from acmecorp_pipeline.json_to_csv import json_to_csv
from acmecorp_pipeline.load_warehouse import load_warehouse
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.transform_csv import transform_csv

log = get_logger("incremental_sync")

MARKER_FILE = Path("/tmp/acmecorp_last_incremental")


def run_incremental_sync(config: PipelineConfig) -> int:
    """Process new files that arrived since the last run.

    Returns the number of files processed.
    """
    lock = Lock("incremental_sync", timeout=30)
    if not lock.acquire():
        log.info("Incremental sync already running, skipping")
        return 0

    try:
        log.info("Starting incremental sync...")

        last_run = 0.0
        if MARKER_FILE.is_file():
            try:
                last_run = float(MARKER_FILE.read_text().strip())
            except (ValueError, OSError):
                last_run = 0.0

        input_dir = config.paths.input_dir
        staging_dir = config.paths.staging_dir
        staging_dir.mkdir(parents=True, exist_ok=True)

        new_files = 0

        for pattern in ("*.csv", "*.json"):
            for datafile in input_dir.glob(pattern):
                if not datafile.is_file():
                    continue

                file_mtime = datafile.stat().st_mtime
                if file_mtime <= last_run:
                    continue

                log.info("New file detected: %s", datafile.name)
                new_files += 1

                outfile = staging_dir / f"{datafile.stem}_transformed.csv"

                if datafile.suffix == ".csv":
                    ok = transform_csv(datafile, outfile, delimiter=config.processing.csv_delimiter)
                elif datafile.suffix == ".json":
                    ok = json_to_csv(datafile, outfile) >= 0
                else:
                    continue

                if ok and outfile.is_file():
                    if load_warehouse(outfile, config):
                        archive_file(datafile, config.paths.archive_dir)
                        datafile.unlink(missing_ok=True)
                        outfile.unlink(missing_ok=True)

        # Update marker
        MARKER_FILE.write_text(str(time.time()))

        if new_files == 0:
            log.info("No new files to process")
        else:
            log.info("Incremental sync complete: processed %d files", new_files)

        return new_files

    finally:
        lock.release()


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)
    run_incremental_sync(cfg)
    sys.exit(0)


if __name__ == "__main__":
    main()
