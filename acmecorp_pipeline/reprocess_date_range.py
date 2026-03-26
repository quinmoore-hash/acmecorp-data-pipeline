"""Reprocess / backfill data for a date range.

Replaces ``reprocess_date_range.sh``.  Re-fetches, transforms, and
loads data for each date in the specified range.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import run_query
from acmecorp_pipeline.fetch_api_data import fetch_api_data
from acmecorp_pipeline.json_to_csv import json_to_csv
from acmecorp_pipeline.load_warehouse import load_warehouse
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert, send_slack
from acmecorp_pipeline.transform_csv import transform_csv

log = get_logger("reprocess_date_range")

VENDORS = ["vendor-a", "vendor-b", "vendor-c"]
ENDPOINTS = {
    "vendor-a": ["orders", "inventory"],
    "vendor-b": ["transactions"],
    "vendor-c": ["shipments"],
}


def _parse_date(s: str) -> date:
    """Parse a ``YYYY-MM-DD`` string."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _date_range(start: date, end: date) -> list[date]:
    """Return a list of dates from *start* to *end* inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def reprocess_date_range(
    config: PipelineConfig,
    start_date: str,
    end_date: str,
    vendors: list[str] | None = None,
    delete_existing: bool = True,
) -> bool:
    """Reprocess data for a date range.

    Args:
        config: Pipeline configuration.
        start_date: Start date (``YYYY-MM-DD``).
        end_date: End date (``YYYY-MM-DD``).
        vendors: Subset of vendors to reprocess (default: all).
        delete_existing: Whether to delete existing data for the date range
            before reloading.

    Returns:
        ``True`` if all dates succeeded.
    """
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    days = _date_range(start, end)
    target_vendors = vendors or VENDORS

    log.info(
        "Reprocessing %d days (%s to %s) for vendors: %s",
        len(days), start_date, end_date, ", ".join(target_vendors),
    )

    with Lock("reprocess", timeout=60):
        staging_dir = config.paths.staging_dir / "reprocess"
        staging_dir.mkdir(parents=True, exist_ok=True)

        total_success = 0
        total_fail = 0

        for day in days:
            day_str = day.isoformat()
            log.info("--- Reprocessing %s ---", day_str)

            for vendor in target_vendors:
                endpoints = ENDPOINTS.get(vendor, [])

                for endpoint in endpoints:
                    # Delete existing data if requested
                    if delete_existing:
                        # Table name is built from hardcoded VENDORS/ENDPOINTS
                        # constants — safe to interpolate.
                        table = f"raw_ingest.{vendor.replace('-', '_')}_{endpoint}"
                        log.info("Deleting existing data for %s on %s", table, day_str)
                        run_query(
                            config,
                            f"DELETE FROM {table} WHERE _load_date = %s;",
                            params=(day_str,),
                            profile="production",
                        )

                    # Fetch
                    json_file = staging_dir / f"{vendor}_{endpoint}_{day_str}.json"
                    n = fetch_api_data(vendor, endpoint, json_file, config)
                    if n < 0:
                        log.error("Fetch failed: %s/%s for %s", vendor, endpoint, day_str)
                        total_fail += 1
                        continue

                    # Convert JSON -> CSV
                    csv_file = staging_dir / f"{vendor}_{endpoint}_{day_str}.csv"
                    if json_to_csv(json_file, csv_file) < 0:
                        log.error("JSON conversion failed: %s", json_file)
                        total_fail += 1
                        continue

                    # Transform
                    transformed = staging_dir / f"{vendor}_{endpoint}_{day_str}_transformed.csv"
                    if not transform_csv(csv_file, transformed, delimiter=config.processing.csv_delimiter):
                        log.error("Transform failed: %s", csv_file)
                        total_fail += 1
                        continue

                    # Load
                    if load_warehouse(transformed, config):
                        total_success += 1
                    else:
                        log.error("Load failed: %s", transformed)
                        total_fail += 1

        log.info(
            "Reprocess complete: %d succeeded, %d failed out of %d days",
            total_success, total_fail, len(days),
        )

        if total_fail > 0:
            alert(
                f"Reprocess {start_date} to {end_date}: {total_fail} failures out of "
                f"{total_success + total_fail} operations",
                "WARNING",
                config,
            )
            return False

        send_slack(
            f"Reprocess complete: {start_date} to {end_date}, {total_success} operations OK",
            "INFO",
            config.slack,
        )
        return True


def main() -> None:
    """CLI entry point matching original ``reprocess_date_range.sh`` interface."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <start_date> <end_date> [vendor ...]")
        sys.exit(1)

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    start_date = sys.argv[1]
    end_date = sys.argv[2]
    vendors = sys.argv[3:] if len(sys.argv) > 3 else None

    ok = reprocess_date_range(cfg, start_date, end_date, vendors)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
