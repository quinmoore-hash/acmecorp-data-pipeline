"""Tests for acmecorp_pipeline.json_to_csv module."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from acmecorp_pipeline.json_to_csv import json_to_csv


class TestJsonToCsv:
    def test_array_of_objects(self, tmp_path: Path) -> None:
        input_file = tmp_path / "data.json"
        output_file = tmp_path / "data.csv"
        input_file.write_text(json.dumps([
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]))

        assert json_to_csv(input_file, output_file) == 2
        assert output_file.is_file()

        with open(output_file) as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["id"] == "1"
        assert rows[0]["name"] == "Alice"

    def test_single_object(self, tmp_path: Path) -> None:
        input_file = tmp_path / "single.json"
        output_file = tmp_path / "single.csv"
        input_file.write_text(json.dumps({"key": "value"}))

        assert json_to_csv(input_file, output_file) == 1

    def test_nested_values_serialised(self, tmp_path: Path) -> None:
        input_file = tmp_path / "nested.json"
        output_file = tmp_path / "nested.csv"
        input_file.write_text(json.dumps([
            {"id": 1, "meta": {"color": "red"}},
        ]))

        json_to_csv(input_file, output_file)

        with open(output_file) as fh:
            reader = csv.DictReader(fh)
            row = next(reader)

        # Nested dict should be JSON-serialised
        assert '"color"' in row["meta"]

    def test_missing_file(self, tmp_path: Path) -> None:
        assert json_to_csv(tmp_path / "missing.json", tmp_path / "out.csv") == -1

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        assert json_to_csv(bad, tmp_path / "out.csv") == -1

    def test_empty_array(self, tmp_path: Path) -> None:
        input_file = tmp_path / "empty.json"
        output_file = tmp_path / "empty.csv"
        input_file.write_text("[]")

        assert json_to_csv(input_file, output_file) == -1

    def test_union_of_keys(self, tmp_path: Path) -> None:
        """Records with different keys should produce a union of all columns."""
        input_file = tmp_path / "union.json"
        output_file = tmp_path / "union.csv"
        input_file.write_text(json.dumps([
            {"a": 1, "b": 2},
            {"a": 3, "c": 4},
        ]))

        assert json_to_csv(input_file, output_file) == 2

        with open(output_file) as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []

        assert "a" in fieldnames
        assert "b" in fieldnames
        assert "c" in fieldnames

    def test_with_sample_data(self, tmp_path: Path) -> None:
        """Test with the actual vendor_b_transactions_sample.json."""
        sample = Path(__file__).parent.parent / "vendor_b_transactions_sample.json"
        if not sample.is_file():
            pytest.skip("Sample data not available")

        output_file = tmp_path / "transactions.csv"
        result = json_to_csv(sample, output_file)
        assert result == 10  # 10 transactions in sample

        with open(output_file) as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 10
        assert rows[0]["transaction_id"] == "TXN-80001"
