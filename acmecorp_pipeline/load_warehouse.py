"""Warehouse data loader — bulk loads transformed CSVs into PostgreSQL.

Replaces ``load_warehouse.sh``.  Uses :mod:`psycopg2` ``COPY`` protocol
instead of shelling out to ``psql``.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import copy_from_csv, get_table_count, run_query
from acmecorp_pipeline.file_utils import check_file
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("load_warehouse")

# Filename pattern -> target table mapping
TABLE_MAP: dict[str, str] = {
    "vendor-a_orders": "raw_ingest.vendor_a_orders",
    "vendor-a_inventory": "raw_ingest.vendor_a_inventory",
    "vendor-b_transactions": "raw_ingest.vendor_b_transactions",
    "vendor-c_shipments": "raw_ingest.vendor_c_shipments",
    "customer_": "raw_ingest.customer_data",
    "product_": "raw_ingest.product_catalog",
}


def _detect_target_table(filename: str) -> Optional[str]:
    """Determine the target table from a filename pattern."""
    # Strip _transformed.csv and trailing date stamps
    basename = filename.replace("_transformed.csv", "")
    import re
    basename = re.sub(r"_\d+$", "", basename)

    for pattern, table in TABLE_MAP.items():
        if basename.startswith(pattern) or pattern in basename:
            return table
    return None


def _read_csv_header(csv_path: Path) -> list[str]:
    """Read and return the header row of a CSV file."""
    with open(csv_path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
    return header or []


def load_warehouse(
    input_file: Path,
    config: PipelineConfig,
    target_table: Optional[str] = None,
    profile: str = "production",
) -> bool:
    """Load a transformed CSV file into the data warehouse.

    The loading process:
    1. Detect or validate the target table
    2. Create a temporary staging table (all TEXT columns)
    3. COPY the CSV into staging
    4. INSERT from staging into the target
    5. Drop the staging table

    Returns ``True`` on success.
    """
    if not check_file(input_file):
        return False

    if target_table is None:
        target_table = _detect_target_table(input_file.name)
        if target_table is None:
            log.error("Cannot determine target table for: %s", input_file.name)
            return False

    log.info("Loading %s -> %s", input_file.name, target_table)

    # Get pre-load row count
    pre_count = get_table_count(config, target_table, profile)

    # Build staging table name
    staging_table = f"{target_table}_staging_{os.getpid()}"

    # Read header to create staging table
    header = _read_csv_header(input_file)
    if not header:
        log.error("Empty CSV header in %s", input_file)
        return False

    # Create staging table with all TEXT columns
    col_defs = ", ".join(f'"{col}" TEXT' for col in header)
    create_sql = f"CREATE TABLE {staging_table} ({col_defs});"

    run_query(config, f"DROP TABLE IF EXISTS {staging_table};", profile=profile)
    result = run_query(config, create_sql, profile=profile)
    if result is None:
        log.error("Failed to create staging table: %s", staging_table)
        return False

    # Bulk load via COPY
    if not copy_from_csv(config, input_file, staging_table, profile):
        log.error("COPY failed for %s", input_file)
        run_query(config, f"DROP TABLE IF EXISTS {staging_table};", profile=profile)
        return False

    # Merge staging into target (INSERT only — no upsert)
    merge_result = run_query(
        config,
        f"INSERT INTO {target_table} SELECT * FROM {staging_table};",
        profile=profile,
    )
    if merge_result is None:
        log.error("Merge failed: %s -> %s", staging_table, target_table)
        run_query(config, f"DROP TABLE IF EXISTS {staging_table};", profile=profile)
        return False

    # Cleanup staging
    run_query(config, f"DROP TABLE IF EXISTS {staging_table};", profile=profile)

    # Verify load
    post_count = get_table_count(config, target_table, profile)
    loaded_rows = post_count - pre_count
    log.info("Loaded %d rows into %s (total: %d)", loaded_rows, target_table, post_count)

    return True


def main() -> None:
    """CLI entry point matching original ``load_warehouse.sh`` interface."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <csv_file> [target_table]")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    target_table = sys.argv[2] if len(sys.argv) > 2 else None

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    ok = load_warehouse(input_file, cfg, target_table)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
