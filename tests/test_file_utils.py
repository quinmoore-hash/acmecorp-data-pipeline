"""Tests for acmecorp_pipeline.file_utils module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acmecorp_pipeline.file_utils import (
    check_file,
    count_data_rows,
    file_size_human,
    validate_csv,
    validate_json,
)


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "test.csv"
    p.write_text("col1,col2,col3\na,b,c\nd,e,f\n")
    return p


@pytest.fixture
def sample_json(tmp_path: Path) -> Path:
    p = tmp_path / "test.json"
    p.write_text(json.dumps([{"a": 1}, {"a": 2}]))
    return p


class TestCheckFile:
    def test_existing_file(self, sample_csv: Path) -> None:
        assert check_file(sample_csv) is True

    def test_missing_file(self, tmp_path: Path) -> None:
        assert check_file(tmp_path / "missing.csv") is False

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.csv"
        p.write_text("")
        assert check_file(p) is False


class TestCountDataRows:
    def test_counts_data_rows_only(self, sample_csv: Path) -> None:
        assert count_data_rows(sample_csv) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.csv"
        p.write_text("")
        assert count_data_rows(p) == 0

    def test_header_only(self, tmp_path: Path) -> None:
        p = tmp_path / "header.csv"
        p.write_text("col1,col2\n")
        assert count_data_rows(p) == 0


class TestValidateCSV:
    def test_valid_csv(self, sample_csv: Path) -> None:
        assert validate_csv(sample_csv) is True

    def test_inconsistent_columns(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.csv"
        p.write_text("a,b,c\n1,2\n")
        assert validate_csv(p) is False


class TestValidateJSON:
    def test_valid_json(self, sample_json: Path) -> None:
        assert validate_json(sample_json) is True

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        assert validate_json(p) is False


class TestFileSizeHuman:
    def test_bytes(self, tmp_path: Path) -> None:
        p = tmp_path / "small.bin"
        p.write_bytes(b"x" * 500)
        assert file_size_human(p) == "500.0 B"

    def test_kilobytes(self, tmp_path: Path) -> None:
        p = tmp_path / "medium.bin"
        p.write_bytes(b"x" * 1024)
        assert file_size_human(p) == "1.0 KB"

    def test_megabytes(self, tmp_path: Path) -> None:
        p = tmp_path / "large.bin"
        p.write_bytes(b"x" * (1024 * 1024))
        assert file_size_human(p) == "1.0 MB"
