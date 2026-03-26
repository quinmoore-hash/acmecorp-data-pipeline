"""Tests for acmecorp_pipeline.data_quality_check module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from acmecorp_pipeline.config import load_config
from acmecorp_pipeline.data_quality_check import (
    CheckResult,
    DQReport,
    _validate_identifier,
    write_report,
)

# ---------------------------------------------------------------------------
# DQReport dataclass
# ---------------------------------------------------------------------------

class TestDQReport:
    def test_empty_report(self) -> None:
        report = DQReport(run_id="test-1", check_date="2025-01-01")
        assert report.checks_run == 0
        assert report.checks_passed == 0
        assert report.checks_warned == 0
        assert report.checks_failed == 0

    def test_mixed_results(self) -> None:
        report = DQReport(run_id="test-2", check_date="2025-01-01")
        report.results.append(CheckResult("a", "PASS", "ok"))
        report.results.append(CheckResult("b", "WARN", "hmm"))
        report.results.append(CheckResult("c", "FAIL", "bad"))
        report.results.append(CheckResult("d", "PASS", "ok"))
        assert report.checks_run == 4
        assert report.checks_passed == 2
        assert report.checks_warned == 1
        assert report.checks_failed == 1


# ---------------------------------------------------------------------------
# _validate_identifier
# ---------------------------------------------------------------------------

class TestValidateIdentifier:
    def test_valid(self) -> None:
        allowed = frozenset(["raw_ingest.vendor_a_orders"])
        assert _validate_identifier("raw_ingest.vendor_a_orders", allowed) == "raw_ingest.vendor_a_orders"

    def test_invalid_raises(self) -> None:
        allowed = frozenset(["raw_ingest.vendor_a_orders"])
        with pytest.raises(ValueError, match="not in allowlist"):
            _validate_identifier("raw_ingest.evil_table", allowed)


# ---------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------

class TestWriteReport:
    def test_creates_report_file(self, tmp_path: Path) -> None:
        report = DQReport(run_id="RPT-1", check_date="2025-06-15")
        report.results.append(CheckResult("chk1", "PASS", "all good"))
        report.results.append(CheckResult("chk2", "FAIL", "bad data"))

        report_file = tmp_path / "reports" / "dq.txt"
        write_report(report, report_file)

        assert report_file.is_file()
        text = report_file.read_text()
        assert "RPT-1" in text
        assert "[PASS] chk1" in text
        assert "[FAIL] chk2" in text
        assert "Total Checks:  2" in text
        assert "Passed:        1" in text
        assert "Failed:        1" in text


# ---------------------------------------------------------------------------
# run_data_quality_checks (mocked DB)
# ---------------------------------------------------------------------------

class TestRunDataQualityChecks:
    def test_all_pass_returns_zero(self, tmp_path: Path) -> None:
        """When every query returns healthy data, exit code should be 0."""
        cfg = load_config(tmp_path)
        # Override log dir to tmp so report file is writable
        cfg.paths.log_dir = tmp_path

        def _fake_query(config, sql, params=None, profile=None):
            # Null-rate checks: return 0%
            if "SUM(CASE" in sql:
                return [("0",)]
            # Freshness checks: return true
            if "EXISTS" in sql:
                return [("t",)]
            # Duplicate / business-rule counts: return 0
            if "HAVING COUNT" in sql or "< 0" in sql or "> CURRENT_DATE" in sql or "> 1000000" in sql:
                return [("0",)]
            # Row counts: return 100
            return [("100",)]

        with patch("acmecorp_pipeline.data_quality_check.run_query", side_effect=_fake_query), \
             patch("acmecorp_pipeline.data_quality_check.send_slack"), \
             patch("acmecorp_pipeline.data_quality_check.alert"):
            from acmecorp_pipeline.data_quality_check import run_data_quality_checks
            code = run_data_quality_checks(cfg, run_id="TEST-1")

        # Null=0%, counts consistent, freshness=true, no dups/violations => all pass
        assert code == 0

    def test_failures_return_one(self, tmp_path: Path) -> None:
        """When null rate exceeds threshold, exit code should be 1."""
        cfg = load_config(tmp_path)
        cfg.paths.log_dir = tmp_path

        call_count = 0

        def fake_query(config, sql, params=None, profile=None):
            nonlocal call_count
            call_count += 1
            # For null-rate checks return a high null %
            if "SUM(CASE" in sql:
                return [("99",)]
            # Freshness
            if "EXISTS" in sql:
                return [("t",)]
            return [("100",)]

        with patch("acmecorp_pipeline.data_quality_check.run_query", side_effect=fake_query), \
             patch("acmecorp_pipeline.data_quality_check.send_slack"), \
             patch("acmecorp_pipeline.data_quality_check.alert"):
            from acmecorp_pipeline.data_quality_check import run_data_quality_checks
            code = run_data_quality_checks(cfg, run_id="TEST-F")

        assert code == 1  # critical failures
