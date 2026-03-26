"""ETL master orchestrator — nightly pipeline that fetches, transforms,
loads, and validates data from all vendor sources.

Replaces ``etl_master.sh``.  Runs nightly at 2 AM EST via cron.

Exit codes (preserved from the original):
- 0: success
- 1: critical failure (pipeline aborted)
- 2: completed with warnings
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.data_quality_check import run_data_quality_checks
from acmecorp_pipeline.db_helpers import check_db_connection
from acmecorp_pipeline.fetch_api_data import fetch_api_data
from acmecorp_pipeline.file_utils import archive_file
from acmecorp_pipeline.json_to_csv import json_to_csv
from acmecorp_pipeline.legacy_ftp_sync import run_ftp_sync
from acmecorp_pipeline.load_warehouse import load_warehouse
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert, send_slack
from acmecorp_pipeline.retry_failed_loads import retry_failed_loads
from acmecorp_pipeline.s3_sync import s3_archive, s3_sync_incoming
from acmecorp_pipeline.transform_csv import transform_csv
from acmecorp_pipeline.vendor_data_fix import fix_vendor_data

log = get_logger("etl_master")

# Vendor/endpoint matrix
VENDORS = {
    "vendor-a": ["orders", "inventory"],
    "vendor-b": ["transactions"],
    "vendor-c": ["shipments"],
}


def _step_banner(step: str) -> None:
    """Log a step separator for readability."""
    log.info("=" * 60)
    log.info("STEP: %s", step)
    log.info("=" * 60)


def run_etl(config: PipelineConfig) -> int:
    """Execute the full nightly ETL pipeline.

    Steps (matching ``etl_master.sh``):
    1. Pre-flight checks
    2. S3 sync incoming files
    3. Legacy FTP sync
    4. API data fetch (all vendors)
    5. Vendor data fixes
    6. CSV transformation
    7. JSON → CSV conversion
    8. Warehouse loading
    9. Retry previously failed loads
    10. Data quality checks
    11. Archive processed files
    12. S3 archive sync
    13. Notification / summary

    Returns exit code: 0 = success, 1 = critical, 2 = warnings.
    """
    run_id = f"ETL_{datetime.now():%Y%m%d_%H%M%S}"
    start_time = time.time()
    warnings: list[str] = []
    today_str = date.today().isoformat()

    log.info("=" * 60)
    log.info("AcmeCorp Nightly ETL Pipeline — %s", run_id)
    log.info("=" * 60)
    send_slack(f"ETL pipeline started: {run_id}", "INFO", config.slack)

    # ------------------------------------------------------------------
    # Step 1: Pre-flight checks
    # ------------------------------------------------------------------
    _step_banner("Pre-flight checks")

    for profile_name in config.db_profiles:
        if not check_db_connection(config, profile_name):
            log.error("FATAL: Database '%s' unreachable — aborting", profile_name)
            alert(f"ETL {run_id} ABORTED: database '{profile_name}' unreachable", "CRITICAL", config)
            return 1

    # Ensure directories exist
    for d in [config.paths.input_dir, config.paths.processed_dir,
              config.paths.archive_dir, config.paths.staging_dir,
              config.paths.error_dir]:
        d.mkdir(parents=True, exist_ok=True)

    log.info("Pre-flight checks passed")

    # ------------------------------------------------------------------
    # Step 2: S3 sync incoming
    # ------------------------------------------------------------------
    _step_banner("S3 sync incoming")
    try:
        n = s3_sync_incoming(config)
        if n < 0:
            warnings.append("S3 sync returned errors")
        else:
            log.info("S3 sync: %d new files", n)
    except Exception as exc:
        log.warning("S3 sync failed: %s", exc)
        warnings.append(f"S3 sync failed: {exc}")

    # ------------------------------------------------------------------
    # Step 3: Legacy FTP sync
    # ------------------------------------------------------------------
    _step_banner("Legacy FTP sync")
    try:
        if not run_ftp_sync(config):
            warnings.append("FTP sync completed with errors")
    except Exception as exc:
        log.warning("FTP sync failed: %s", exc)
        warnings.append(f"FTP sync failed: {exc}")

    # ------------------------------------------------------------------
    # Step 4: API data fetch
    # ------------------------------------------------------------------
    _step_banner("API data fetch")
    fetch_results: dict[str, int] = {}

    for vendor, endpoints in VENDORS.items():
        for endpoint in endpoints:
            json_file = config.paths.input_dir / f"{vendor}_{endpoint}_{today_str}.json"
            n = fetch_api_data(vendor, endpoint, json_file, config)
            key = f"{vendor}/{endpoint}"
            fetch_results[key] = n
            if n < 0:
                warnings.append(f"API fetch failed: {key}")
            else:
                log.info("Fetched %d records: %s", n, key)

    # ------------------------------------------------------------------
    # Step 5: Vendor data fixes
    # ------------------------------------------------------------------
    _step_banner("Vendor data fixes")
    for csv_file in config.paths.input_dir.glob("*.csv"):
        vendor_key: Optional[str] = None
        for v in VENDORS:
            if csv_file.name.startswith(v):
                vendor_key = v
                break
        if vendor_key:
            fixed_file = config.paths.staging_dir / f"{csv_file.stem}_fixed.csv"
            if fix_vendor_data(csv_file, fixed_file, vendor_key):
                csv_file.unlink(missing_ok=True)
                fixed_file.rename(csv_file)
            else:
                warnings.append(f"Vendor fix failed: {csv_file.name}")

    # ------------------------------------------------------------------
    # Step 6: CSV transformation
    # ------------------------------------------------------------------
    _step_banner("CSV transformation")
    transformed_files: list[Path] = []

    for csv_file in config.paths.input_dir.glob("*.csv"):
        out_file = config.paths.staging_dir / f"{csv_file.stem}_transformed.csv"
        if transform_csv(csv_file, out_file, delimiter=config.processing.csv_delimiter):
            transformed_files.append(out_file)
        else:
            warnings.append(f"Transform failed: {csv_file.name}")

    # ------------------------------------------------------------------
    # Step 7: JSON -> CSV conversion
    # ------------------------------------------------------------------
    _step_banner("JSON to CSV conversion")

    for json_file in config.paths.input_dir.glob("*.json"):
        csv_out = config.paths.staging_dir / f"{json_file.stem}.csv"
        n = json_to_csv(json_file, csv_out)
        if n >= 0:
            # Transform the converted CSV
            transformed = config.paths.staging_dir / f"{json_file.stem}_transformed.csv"
            if transform_csv(csv_out, transformed, delimiter=config.processing.csv_delimiter):
                transformed_files.append(transformed)
            csv_out.unlink(missing_ok=True)
        else:
            warnings.append(f"JSON conversion failed: {json_file.name}")

    # ------------------------------------------------------------------
    # Step 8: Warehouse loading
    # ------------------------------------------------------------------
    _step_banner("Warehouse loading")
    loaded = 0
    failed = 0

    for tf in transformed_files:
        if load_warehouse(tf, config):
            loaded += 1
            # Move source to processed
            src = config.paths.input_dir / tf.name.replace("_transformed", "")
            if src.is_file():
                src.rename(config.paths.processed_dir / src.name)
            tf.unlink(missing_ok=True)
        else:
            failed += 1
            # Move to error directory
            error_dest = config.paths.error_dir / tf.name
            try:
                tf.rename(error_dest)
            except OSError:
                pass
            warnings.append(f"Load failed: {tf.name}")

    log.info("Loading: %d succeeded, %d failed", loaded, failed)

    # ------------------------------------------------------------------
    # Step 9: Retry previously failed loads
    # ------------------------------------------------------------------
    _step_banner("Retry failed loads")
    try:
        retry_failed_loads(config)
    except Exception as exc:
        log.warning("Retry failed loads error: %s", exc)

    # ------------------------------------------------------------------
    # Step 10: Data quality checks
    # ------------------------------------------------------------------
    _step_banner("Data quality checks")
    dq_exit = run_data_quality_checks(config, run_id)
    if dq_exit == 1:
        warnings.append("Data quality: CRITICAL failures detected")
    elif dq_exit == 2:
        warnings.append("Data quality: warnings detected")

    # ------------------------------------------------------------------
    # Step 11: Archive processed files
    # ------------------------------------------------------------------
    _step_banner("Archive processed files")
    for processed_file in config.paths.processed_dir.glob("*"):
        if processed_file.is_file():
            archive_file(processed_file, config.paths.archive_dir)

    # ------------------------------------------------------------------
    # Step 12: S3 archive sync
    # ------------------------------------------------------------------
    _step_banner("S3 archive sync")
    try:
        s3_archive(config)
    except Exception as exc:
        log.warning("S3 archive sync failed: %s", exc)
        warnings.append(f"S3 archive sync failed: {exc}")

    # ------------------------------------------------------------------
    # Step 13: Summary and notification
    # ------------------------------------------------------------------
    duration = int(time.time() - start_time)
    duration_min = duration // 60
    duration_sec = duration % 60

    summary_lines = [
        f"ETL Pipeline Complete: {run_id}",
        f"Duration: {duration_min}m {duration_sec}s",
        f"Files loaded: {loaded}",
        f"Files failed: {failed}",
        f"Warnings: {len(warnings)}",
    ]

    if warnings:
        summary_lines.append("\nWarnings:")
        for w in warnings:
            summary_lines.append(f"  - {w}")

    summary = "\n".join(summary_lines)
    log.info("\n%s", summary)

    if failed > 0 or dq_exit == 1:
        alert(f"ETL {run_id} completed with errors:\n{summary}", "WARNING", config)
        return 2
    elif warnings:
        send_slack(f"ETL {run_id} completed with warnings:\n{summary}", "WARNING", config.slack)
        return 2
    else:
        send_slack(f"ETL {run_id} completed successfully:\n{summary}", "INFO", config.slack)
        return 0


def main() -> None:
    """CLI entry point — replaces the ``etl_master.sh`` cron invocation."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    with Lock("etl_master", timeout=300):
        exit_code = run_etl(cfg)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
