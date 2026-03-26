"""Post-load data quality checks against the warehouse.

Replaces ``data_quality_check.sh``.  Checks row count anomalies,
null rates, duplicates, freshness, and business rule violations.

Exit codes (preserved from the original):
- 0: all checks passed
- 1: critical check failed
- 2: warnings only
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import run_query
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert, send_slack

log = get_logger("data_quality_check")

TABLES = [
    "raw_ingest.vendor_a_orders",
    "raw_ingest.vendor_a_inventory",
    "raw_ingest.vendor_b_transactions",
    "raw_ingest.vendor_c_shipments",
    "raw_ingest.customer_data",
    "raw_ingest.product_catalog",
]

# (table, column, max_null_pct)
NULL_CHECKS = [
    ("raw_ingest.vendor_a_orders", "order_id", 0),
    ("raw_ingest.vendor_a_orders", "customer_id", 0),
    ("raw_ingest.vendor_a_orders", "order_date", 0),
    ("raw_ingest.vendor_a_orders", "total_amount", 5),
    ("raw_ingest.vendor_b_transactions", "transaction_id", 0),
    ("raw_ingest.vendor_b_transactions", "amount", 0),
    ("raw_ingest.vendor_c_shipments", "shipment_id", 0),
    ("raw_ingest.vendor_c_shipments", "tracking_number", 10),
    ("raw_ingest.customer_data", "email", 5),
]

# (table, key_column)
DUPLICATE_CHECKS = [
    ("raw_ingest.vendor_a_orders", "order_id"),
    ("raw_ingest.vendor_b_transactions", "transaction_id"),
    ("raw_ingest.vendor_c_shipments", "shipment_id"),
]

FRESHNESS_TABLES = [
    "raw_ingest.vendor_a_orders",
    "raw_ingest.vendor_b_transactions",
    "raw_ingest.vendor_c_shipments",
]


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS", "WARN", "FAIL"
    details: str


@dataclass
class DQReport:
    run_id: str
    check_date: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def checks_run(self) -> int:
        return len(self.results)

    @property
    def checks_passed(self) -> int:
        return sum(1 for r in self.results if r.status == "PASS")

    @property
    def checks_warned(self) -> int:
        return sum(1 for r in self.results if r.status == "WARN")

    @property
    def checks_failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL")


def _query_scalar(config: PipelineConfig, sql: str) -> str:
    """Run a single-value query and return the result as a stripped string."""
    rows = run_query(config, sql, profile="production")
    if rows and rows[0]:
        return str(rows[0][0]).strip()
    return "0"


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def check_row_counts(config: PipelineConfig, report: DQReport) -> None:
    """Check for row count anomalies compared to 7-day average."""
    log.info("Running row count checks...")
    deviation_pct = config.thresholds.row_count_deviation_pct
    check_date = report.check_date

    for table in TABLES:
        today_count = int(_query_scalar(
            config,
            f"SELECT COUNT(*) FROM {table} WHERE _load_date = '{check_date}';",
        ))
        avg_count = int(_query_scalar(
            config,
            f"""SELECT COALESCE(ROUND(AVG(cnt)), 0) FROM (
                SELECT _load_date, COUNT(*) as cnt
                FROM {table}
                WHERE _load_date >= '{check_date}'::date - interval '7 days'
                  AND _load_date < '{check_date}'
                GROUP BY _load_date
            ) t;""",
        ))

        if avg_count == 0:
            status = "WARN" if today_count == 0 else "PASS"
            details = ("No data today and no historical baseline" if today_count == 0
                       else f"First load: {today_count} rows")
            report.results.append(CheckResult(f"row_count:{table}", status, details))
            continue

        deviation = abs((today_count - avg_count) * 100 // avg_count) if avg_count else 0

        if deviation > deviation_pct:
            if today_count == 0:
                report.results.append(CheckResult(
                    f"row_count:{table}", "FAIL",
                    f"ZERO rows today (avg: {avg_count}). Possible data feed failure.",
                ))
            else:
                report.results.append(CheckResult(
                    f"row_count:{table}", "WARN",
                    f"Row count deviation {deviation}% (today: {today_count}, avg: {avg_count})",
                ))
        else:
            report.results.append(CheckResult(
                f"row_count:{table}", "PASS",
                f"Row count OK (today: {today_count}, avg: {avg_count}, dev: {deviation}%)",
            ))


def check_null_rates(config: PipelineConfig, report: DQReport) -> None:
    """Check null/empty rates for critical columns."""
    log.info("Running null rate checks...")
    check_date = report.check_date

    for table, column, max_null_pct in NULL_CHECKS:
        result = _query_scalar(
            config,
            f"""SELECT ROUND(100.0 * SUM(CASE WHEN "{column}" IS NULL OR "{column}" = '' THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 2)
            FROM {table}
            WHERE _load_date = '{check_date}';""",
        )
        null_pct = float(result) if result else 0.0
        null_pct_int = int(null_pct)

        if null_pct_int > max_null_pct:
            report.results.append(CheckResult(
                f"null_rate:{table}.{column}", "FAIL",
                f"Null rate {result}% exceeds threshold {max_null_pct}%",
            ))
        else:
            report.results.append(CheckResult(
                f"null_rate:{table}.{column}", "PASS",
                f"Null rate {result}% within threshold {max_null_pct}%",
            ))


def check_duplicates(config: PipelineConfig, report: DQReport) -> None:
    """Check for duplicate keys loaded today."""
    log.info("Running duplicate checks...")
    check_date = report.check_date

    for table, key_col in DUPLICATE_CHECKS:
        dup_count = int(_query_scalar(
            config,
            f"""SELECT COUNT(*) FROM (
                SELECT "{key_col}", COUNT(*)
                FROM {table}
                WHERE _load_date = '{check_date}'
                GROUP BY "{key_col}"
                HAVING COUNT(*) > 1
            ) t;""",
        ))

        if dup_count > 0:
            report.results.append(CheckResult(
                f"duplicates:{table}.{key_col}", "WARN",
                f"{dup_count} duplicate keys found",
            ))
        else:
            report.results.append(CheckResult(
                f"duplicates:{table}.{key_col}", "PASS",
                "No duplicates found",
            ))


def check_freshness(config: PipelineConfig, report: DQReport) -> None:
    """Check whether data was loaded today."""
    log.info("Running freshness checks...")
    check_date = report.check_date

    for table in FRESHNESS_TABLES:
        has_today = _query_scalar(
            config,
            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE _load_date = '{check_date}');",
        )

        if has_today in ("t", "true", "True"):
            report.results.append(CheckResult(
                f"freshness:{table}", "PASS", "Data loaded today",
            ))
        else:
            last_load = _query_scalar(
                config,
                f"SELECT MAX(_load_date) FROM {table};",
            )
            report.results.append(CheckResult(
                f"freshness:{table}", "WARN",
                f"No data today. Last load: {last_load or 'never'}",
            ))


def check_business_rules(config: PipelineConfig, report: DQReport) -> None:
    """Check business rule violations."""
    log.info("Running business rule checks...")
    check_date = report.check_date

    # Negative amounts
    neg_amounts = int(_query_scalar(
        config,
        f"""SELECT COUNT(*) FROM raw_ingest.vendor_a_orders
        WHERE _load_date = '{check_date}' AND total_amount::numeric < 0;""",
    ))
    report.results.append(CheckResult(
        "biz_rule:negative_amounts",
        "WARN" if neg_amounts > 0 else "PASS",
        f"{neg_amounts} orders with negative amounts" if neg_amounts > 0 else "No negative amounts",
    ))

    # Future ship dates
    future_dates = int(_query_scalar(
        config,
        f"""SELECT COUNT(*) FROM raw_ingest.vendor_c_shipments
        WHERE _load_date = '{check_date}' AND ship_date::date > CURRENT_DATE;""",
    ))
    report.results.append(CheckResult(
        "biz_rule:future_ship_dates",
        "WARN" if future_dates > 0 else "PASS",
        f"{future_dates} shipments with future dates" if future_dates > 0 else "No future ship dates",
    ))

    # Transaction amount outliers
    outliers = int(_query_scalar(
        config,
        f"""SELECT COUNT(*) FROM raw_ingest.vendor_b_transactions
        WHERE _load_date = '{check_date}' AND ABS(amount::numeric) > 1000000;""",
    ))
    report.results.append(CheckResult(
        "biz_rule:amount_outliers",
        "WARN" if outliers > 0 else "PASS",
        f"{outliers} transactions over $1M threshold" if outliers > 0 else "No amount outliers",
    ))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(report: DQReport, report_file: Path) -> None:
    """Write the DQ report to a text file."""
    report_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "=========================================",
        f"Data Quality Report - {report.run_id}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "=========================================",
    ]
    for r in report.results:
        lines.append(f"[{r.status}] {r.name}: {r.details}")
    lines += [
        "",
        "=========================================",
        f"Data Quality Summary - {report.run_id}",
        f"Date: {report.check_date}",
        "=========================================",
        f"Total Checks:  {report.checks_run}",
        f"Passed:        {report.checks_passed}",
        f"Warnings:      {report.checks_warned}",
        f"Failed:        {report.checks_failed}",
        "=========================================",
    ]
    report_file.write_text("\n".join(lines) + "\n")
    log.info("DQ Report written to: %s", report_file)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_data_quality_checks(
    config: PipelineConfig,
    run_id: Optional[str] = None,
) -> int:
    """Execute all data quality checks.

    Returns exit code: 0 = pass, 1 = critical fail, 2 = warnings only.
    """
    if run_id is None:
        run_id = f"DQ_{datetime.now():%Y%m%d_%H%M%S}"

    check_date = date.today().isoformat()
    report = DQReport(run_id=run_id, check_date=check_date)
    report_file = config.paths.log_dir / f"dq_report_{date.today():%Y%m%d}.txt"

    log.info("Starting Data Quality Checks: %s", run_id)

    check_row_counts(config, report)
    check_null_rates(config, report)
    check_duplicates(config, report)
    check_freshness(config, report)
    check_business_rules(config, report)

    write_report(report, report_file)

    if report.checks_failed > 0:
        alert(
            f"DQ Check {run_id}: {report.checks_failed} FAILED checks out of {report.checks_run}. "
            f"See {report_file}",
            "CRITICAL",
            config,
        )
        return 1
    elif report.checks_warned > 0:
        send_slack(
            f"DQ Check {run_id}: {report.checks_warned} warnings out of {report.checks_run} checks. "
            f"See {report_file}",
            "WARNING",
            config.slack,
        )
        return 2
    else:
        log.info("All %d data quality checks passed", report.checks_run)
        return 0


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    exit_code = run_data_quality_checks(cfg, run_id)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
