"""Tests for acmecorp_pipeline.vendor_data_fix module."""

from __future__ import annotations

from pathlib import Path

import pytest

from acmecorp_pipeline.vendor_data_fix import (
    _fix_vendor_a,
    _fix_vendor_b,
    _fix_vendor_c,
    fix_vendor_data,
)


class TestFixVendorA:
    def test_comma_decimal(self) -> None:
        rows = [{"order_total": "1.234,56", "state": "IL"}]
        result = _fix_vendor_a(rows, [])
        assert result[0]["order_total"] == "1.234.56"

    def test_state_name_to_code(self) -> None:
        rows = [{"state": "California"}]
        result = _fix_vendor_a(rows, [])
        assert result[0]["state"] == "CA"

    def test_already_code_unchanged(self) -> None:
        rows = [{"state": "IL"}]
        result = _fix_vendor_a(rows, [])
        assert result[0]["state"] == "IL"


class TestFixVendorB:
    def test_currency_symbol_stripped(self) -> None:
        rows = [{"amount": "$1250.00"}]
        result = _fix_vendor_b(rows, [])
        assert result[0]["amount"] == "1250.00"

    def test_euro_symbol(self) -> None:
        rows = [{"amount": "€899.00"}]
        result = _fix_vendor_b(rows, [])
        assert result[0]["amount"] == "899.00"

    def test_dd_mm_yyyy_to_iso(self) -> None:
        rows = [{"transaction_date": "15-01-2024"}]
        result = _fix_vendor_b(rows, [])
        assert result[0]["transaction_date"] == "2024-01-15"


class TestFixVendorC:
    def test_weight_lbs_to_kg(self) -> None:
        rows = [{"weight_kg": "10lb"}]
        result = _fix_vendor_c(rows, [])
        assert float(result[0]["weight_kg"]) == pytest.approx(4.536, abs=0.01)

    def test_column_rename(self) -> None:
        rows = [{"shipTo": "address", "trackingNum": "123"}]
        result = _fix_vendor_c(rows, [])
        assert "ship_to" in result[0]
        assert "tracking_number" in result[0]
        assert "shipTo" not in result[0]


class TestFixVendorData:
    def test_end_to_end_vendor_a(self, tmp_path: Path) -> None:
        input_file = tmp_path / "in.csv"
        output_file = tmp_path / "out.csv"
        input_file.write_text(
            "order_id,total_amount,state\n"
            'ORD-1,"1.234,56",California\n'
            "ORD-2,100.00,IL\n"
        )

        assert fix_vendor_data(input_file, output_file, "vendor-a") is True
        assert output_file.is_file()

        lines = output_file.read_text().splitlines()
        assert len(lines) == 3  # header + 2 rows
        # Verify comma-decimal was converted to dot-decimal
        assert "1.234.56" in lines[1]

    def test_unknown_vendor(self, tmp_path: Path) -> None:
        input_file = tmp_path / "in.csv"
        input_file.write_text("a\n1\n")
        assert fix_vendor_data(input_file, tmp_path / "out.csv", "vendor-x") is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert fix_vendor_data(tmp_path / "missing.csv", tmp_path / "out.csv", "vendor-a") is False
