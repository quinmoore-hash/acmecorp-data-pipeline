"""Tests for acmecorp_pipeline.transform_csv module."""

from __future__ import annotations

from pathlib import Path

import pytest

from acmecorp_pipeline.transform_csv import (
    _clean_null,
    _normalise_date,
    _normalise_phone,
    transform_csv,
)


class TestNormaliseDate:
    def test_us_to_iso(self) -> None:
        assert _normalise_date("01/15/2024") == "2024-01-15"

    def test_single_digit_month_day(self) -> None:
        assert _normalise_date("1/5/2024") == "2024-01-05"

    def test_non_date_unchanged(self) -> None:
        assert _normalise_date("2024-01-15") == "2024-01-15"

    def test_empty_string(self) -> None:
        assert _normalise_date("") == ""


class TestNormalisePhone:
    def test_parens_format(self) -> None:
        assert _normalise_phone("(555) 123-4567") == "+15551234567"

    def test_dots_format(self) -> None:
        assert _normalise_phone("555.234.5678") == "+15552345678"

    def test_dashes_format(self) -> None:
        assert _normalise_phone("555-456-7890") == "+15554567890"

    def test_spaces_format(self) -> None:
        assert _normalise_phone("555 678 9012") == "+15556789012"

    def test_international_unchanged(self) -> None:
        assert _normalise_phone("+86-10-1234-5678") == "+86-10-1234-5678"


class TestCleanNull:
    def test_null_variants(self) -> None:
        assert _clean_null("NULL") == ""
        assert _clean_null("null") == ""
        assert _clean_null("N/A") == ""
        assert _clean_null("n/a") == ""
        assert _clean_null("None") == ""
        assert _clean_null("nil") == ""

    def test_non_null_unchanged(self) -> None:
        assert _clean_null("hello") == "hello"
        assert _clean_null("123") == "123"


class TestTransformCSV:
    def test_basic_transform(self, tmp_path: Path) -> None:
        input_file = tmp_path / "input.csv"
        output_file = tmp_path / "output.csv"

        input_file.write_text(
            "name,date,phone\n"
            "John,01/15/2024,(555) 123-4567\n"
            "Jane,12/25/2023,555.234.5678\n"
        )

        assert transform_csv(input_file, output_file) is True
        assert output_file.is_file()

        lines = output_file.read_text().splitlines()
        assert len(lines) == 3  # header + 2 data rows
        # Header should have metadata columns
        assert "_load_date" in lines[0]
        assert "_source_file" in lines[0]

    def test_date_normalisation_in_output(self, tmp_path: Path) -> None:
        input_file = tmp_path / "input.csv"
        output_file = tmp_path / "output.csv"

        input_file.write_text("date\n01/15/2024\n")
        transform_csv(input_file, output_file)

        content = output_file.read_text()
        assert "2024-01-15" in content

    def test_empty_rows_skipped(self, tmp_path: Path) -> None:
        input_file = tmp_path / "input.csv"
        output_file = tmp_path / "output.csv"

        input_file.write_text("a,b\n1,2\n,,\n3,4\n")
        transform_csv(input_file, output_file)

        lines = output_file.read_text().splitlines()
        assert len(lines) == 3  # header + 2 data rows (empty row skipped)

    def test_null_values_cleaned(self, tmp_path: Path) -> None:
        input_file = tmp_path / "input.csv"
        output_file = tmp_path / "output.csv"

        input_file.write_text("name,value\nJohn,NULL\nJane,N/A\n")
        transform_csv(input_file, output_file)

        content = output_file.read_text()
        assert "NULL" not in content.split("\n", 1)[1]  # skip header check
        assert "N/A" not in content.split("\n", 1)[1]

    def test_missing_input_returns_false(self, tmp_path: Path) -> None:
        assert transform_csv(tmp_path / "missing.csv", tmp_path / "out.csv") is False

    def test_with_sample_data(self, tmp_path: Path) -> None:
        """Test with the actual vendor_a_orders_sample.csv."""
        sample = Path(__file__).parent.parent / "vendor_a_orders_sample.csv"
        if not sample.is_file():
            pytest.skip("Sample data not available")

        output_file = tmp_path / "transformed.csv"
        assert transform_csv(sample, output_file) is True

        lines = output_file.read_text().splitlines()
        # 15 data rows + 1 header
        assert len(lines) == 16
