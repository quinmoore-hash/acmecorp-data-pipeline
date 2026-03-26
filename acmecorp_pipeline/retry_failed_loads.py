"""Retry failed warehouse loads — scans the error directory and retries.

Replaces ``retry_failed_loads.sh``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.load_warehouse import load_warehouse
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert, send_slack

log = get_logger("retry_failed_loads")

MAX_RETRIES = 3


def _get_retry_count(filepath: Path) -> int:
    """Read the retry count from a sidecar ``.retries`` file."""
    retries_file = filepath.with_suffix(filepath.suffix + ".retries")
    if retries_file.is_file():
        try:
            return int(retries_file.read_text().strip())
        except (ValueError, OSError):
            return 0
    return 0


def _set_retry_count(filepath: Path, count: int) -> None:
    """Write the retry count to a sidecar ``.retries`` file."""
    retries_file = filepath.with_suffix(filepath.suffix + ".retries")
    retries_file.write_text(str(count))


def retry_failed_loads(config: PipelineConfig) -> int:
    """Scan the error directory and retry failed CSV loads.

    Returns the number of successfully retried files.
    """
    error_dir = config.paths.error_dir
    if not error_dir.is_dir():
        log.info("No error directory found")
        return 0

    csv_files = sorted(error_dir.glob("*.csv"))
    if not csv_files:
        log.info("No failed files to retry")
        return 0

    log.info("Found %d failed files to retry", len(csv_files))

    with Lock("retry_failed_loads", timeout=30):
        success_count = 0
        permanent_fail = 0

        for csv_file in csv_files:
            retry_count = _get_retry_count(csv_file)

            if retry_count >= MAX_RETRIES:
                log.warning(
                    "Skipping %s — exceeded max retries (%d/%d)",
                    csv_file.name, retry_count, MAX_RETRIES,
                )
                permanent_fail += 1
                continue

            log.info("Retrying %s (attempt %d/%d)", csv_file.name, retry_count + 1, MAX_RETRIES)

            if load_warehouse(csv_file, config):
                log.info("Retry succeeded: %s", csv_file.name)
                success_count += 1
                # Move to processed and clean up sidecar
                dest = config.paths.processed_dir / csv_file.name
                shutil.move(str(csv_file), str(dest))
                retries_file = csv_file.with_suffix(csv_file.suffix + ".retries")
                retries_file.unlink(missing_ok=True)
            else:
                new_count = retry_count + 1
                _set_retry_count(csv_file, new_count)
                log.warning(
                    "Retry failed: %s (attempt %d/%d)",
                    csv_file.name, new_count, MAX_RETRIES,
                )
                if new_count >= MAX_RETRIES:
                    permanent_fail += 1

        log.info(
            "Retry summary: %d succeeded, %d permanently failed, %d remaining",
            success_count, permanent_fail, len(csv_files) - success_count - permanent_fail,
        )

        if permanent_fail > 0:
            alert(
                f"Retry loads: {permanent_fail} files permanently failed after {MAX_RETRIES} retries. "
                f"Manual intervention required.",
                "WARNING",
                config,
            )

        if success_count > 0:
            send_slack(
                f"Retry loads: {success_count} files successfully reloaded",
                "INFO",
                config.slack,
            )

        return success_count


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)
    retry_failed_loads(cfg)
    sys.exit(0)


if __name__ == "__main__":
    main()
