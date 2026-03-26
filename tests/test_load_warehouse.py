"""Tests for acmecorp_pipeline.load_warehouse module."""

from __future__ import annotations

from pathlib import Path

import pytest

from acmecorp_pipeline.load_warehouse import (
    _detect_target_table,
    _read_csv_header,
    _validate_table,
)


class TestValidateTable:
    def test_valid_table(self) -> None:
        assert _validate_table("raw_ingest.vendor_a_orders") == "raw_ingest.vendor_a_orders"

    def test_invalid_table_raises(self) -> None:
        with pytest.raises(ValueError, match="not in allowlist"):
            _validate_table("raw_ingest.evil_table")


class TestDetectTargetTable:
    def test_vendor_a_orders(self) -> None:
        assert _detect_target_table("vendor-a_orders_20250101_transformed.csv") == "raw_ingest.vendor_a_orders"

    def test_vendor_b_transactions(self) -> None:
        assert _detect_target_table("vendor-b_transactions_transformed.csv") == "raw_ingest.vendor_b_transactions"

    def test_customer_data(self) -> None:
        assert _detect_target_table("customer_data_20250101_transformed.csv") == "raw_ingest.customer_data"

    def test_unknown_returns_none(self) -> None:
        assert _detect_target_table("mystery_file.csv") is None


class TestReadCsvHeader:
    def test_reads_header(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col_a,col_b,col_c\n1,2,3\n")
        assert _read_csv_header(csv_file) == ["col_a", "col_b", "col_c"]

    def test_empty_file(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")
        assert _read_csv_header(csv_file) == []
