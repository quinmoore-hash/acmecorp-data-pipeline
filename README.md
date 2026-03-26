# AcmeCorp Data Pipeline

> **⚠️ Legacy System** — This codebase is scheduled for migration to Python/Airflow as part of the Q3 2024 cloud modernization initiative.

## Overview

Bash-based data pipeline system that orchestrates nightly ETL jobs, ingesting data from multiple vendor APIs and file drops, transforming it, and loading it into a PostgreSQL data warehouse. The system has been in production since 2020 and handles ~50K rows/day across 6 source tables.

## Architecture

```
Vendor APIs (A, B, C)  ──┐
Legacy FTP server ────────┼──► Ingest ──► Validate ──► Transform ──► Load (PostgreSQL)
S3 file drops ────────────┘                                              │
                                                                         ▼
                                                              Data Quality Checks
                                                                         │
                                                                         ▼
                                                                Archive to S3
```

## Repository Structure

```
├── scripts/                    # Main pipeline scripts
│   ├── etl_master.sh           # Primary nightly orchestrator (runs at 2am EST)
│   ├── fetch_api_data.sh       # Pulls data from vendor REST APIs with pagination
│   ├── transform_csv.sh        # Cleans/standardizes CSV files
│   ├── json_to_csv.sh          # Converts JSON feeds to CSV for loading
│   ├── load_warehouse.sh       # Bulk loads CSVs into PostgreSQL via COPY
│   ├── data_quality_check.sh   # Post-load validation (row counts, nulls, dupes)
│   ├── incremental_sync.sh     # Hourly lightweight sync for mid-day drops
│   ├── cron_scheduler.sh       # Installs/manages all pipeline cron jobs
│   ├── log_monitor.sh          # Scans logs for errors, sends alerts
│   ├── db_backup.sh            # Daily incremental + weekly full DB backups
│   ├── db_restore.sh           # Restores from backup
│   ├── health_check.sh         # 5-minute system health checks
│   ├── cleanup_old_data.sh     # Daily cleanup of old files and logs
│   ├── setup_environment.sh    # One-time server provisioning
│   ├── generate_report.sh      # Monthly summary reports
│   ├── s3_sync.sh              # Upload/download data to/from S3
│   ├── check_dependencies.sh   # Verifies all tools and services
│   ├── parse_access_logs.sh    # API access log analysis
│   ├── reprocess_date_range.sh # Backfill/reprocess historical data
│   ├── retry_failed_loads.sh   # Re-processes files in the error directory
│   ├── legacy_ftp_sync.sh      # FTP download from legacy vendor (fragile)
│   ├── vendor_data_fix.sh      # Ad-hoc data fixes for vendor quirks
│   └── deploy_hotfix.sh        # Manual SCP-based deployment (no CI/CD)
│
├── utils/                      # Shared helper libraries (sourced by scripts)
│   ├── logging.sh              # log_info, log_warn, log_error, log_debug
│   ├── notify.sh               # Slack, email, PagerDuty alerts
│   ├── file_utils.sh           # File validation, archiving, CSV/JSON checks
│   ├── db_helpers.sh           # PostgreSQL query wrappers
│   └── lock_manager.sh         # File-based job locking
│
├── configs/                    # Environment and service configuration
│   ├── pipeline.env            # Main environment variables
│   ├── database.conf           # DB connection profiles (prod, staging, reporting)
│   └── alerting.conf           # Alert thresholds and routing
│
├── data/                       # Sample data files for testing
│   ├── vendor_a_orders_sample.csv
│   ├── vendor_b_transactions_sample.json
│   ├── vendor_c_shipments_sample.csv
│   ├── customer_data_sample.csv
│   └── product_catalog_sample.json
│
└── logs/                       # Sample log outputs
    ├── pipeline_20240118.log
    └── dq_report_20240118.txt
```

## Cron Schedule

| Schedule | Script | Description |
|---|---|---|
| `0 2 * * *` | `etl_master.sh` | Nightly full ETL pipeline |
| `0 * * * *` | `incremental_sync.sh` | Hourly incremental data sync |
| `*/15 * * * *` | `log_monitor.sh` | Log scanning and alerting |
| `0 1 * * *` | `db_backup.sh` | Daily incremental DB backup |
| `0 3 * * 0` | `db_backup.sh --full` | Weekly full DB backup |
| `0 6 * * *` | `cleanup_old_data.sh` | Old file and log cleanup |
| `*/5 * * * *` | `health_check.sh` | System health checks |
| `0 8 1 * *` | `generate_report.sh` | Monthly summary report |

## Dependencies

- **bash** 4.0+
- **curl**, **jq**, **awk**, **sed**, **grep**, **gzip**
- **psql** (PostgreSQL client)
- **aws** CLI v2
- **mailx** or **sendmail** (for email alerts)
- **ftp** (for legacy vendor sync)
- **python3** (optional, used in `vendor_data_fix.sh`)

## Known Issues / Technical Debt

- **No CI/CD** — deployments are manual SCP via `deploy_hotfix.sh`
- **Hardcoded credentials** in `pipeline.env` and `legacy_ftp_sync.sh`
- **No proper secret management** — passwords in plaintext config files
- **Duplicated logic** — CSV cleanup code exists in both `transform_csv.sh` and `vendor_data_fix.sh`
- **Brittle FTP sync** — `legacy_ftp_sync.sh` has no error handling and hardcoded field positions
- **Race condition** — `cleanup_old_data.sh` can delete temp/lock files while jobs are running
- **No unit tests** — validation is manual
- **File-based locking** — `lock_manager.sh` can leave stale locks on SIGKILL
- **Date parsing** — `transform_csv.sh` assumes MM/DD/YYYY and breaks on European formats
- **No idempotent loads** — `load_warehouse.sh` does INSERT only, no upsert/dedup

## Team Contacts

| Role | Name | Email |
|---|---|---|
| Pipeline Lead | J. Thompson | jthompson@acmecorp.com |
| Data Engineer | M. Chen | mchen@acmecorp.com |
| SRE | A. Singh | asingh@acmecorp.com |
| Data Ops (on-call) | Team | data-ops@acmecorp.com |
