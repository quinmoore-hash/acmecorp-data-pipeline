"""Tests for acmecorp_pipeline.config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from acmecorp_pipeline.config import (
    PipelineConfig,
    _load_env_file,
    _load_ini,
    load_config,
)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """Create a minimal pipeline.env file."""
    p = tmp_path / "pipeline.env"
    p.write_text(
        'BASE_DIR="/opt/test"\n'
        'LOG_DIR="/var/log/test"\n'
        'API_KEY="test-key-123"\n'
        "API_TIMEOUT=30\n"
        "RETRY_COUNT=3\n"
        'S3_BUCKET="test-bucket"\n'
        'S3_REGION="us-east-1"\n'
        "# This is a comment\n"
        "\n"
        'CSV_DELIMITER=","\n'
    )
    return p


@pytest.fixture
def ini_file(tmp_path: Path) -> Path:
    """Create a minimal INI config file."""
    p = tmp_path / "database.conf"
    p.write_text(
        "[production]\n"
        "host = localhost\n"
        "port = 5432\n"
        "dbname = testdb\n"
        "user = testuser\n"
        "password = testpass\n"
    )
    return p


class TestLoadEnvFile:
    def test_basic_parsing(self, env_file: Path) -> None:
        result = _load_env_file(env_file)
        assert result["BASE_DIR"] == "/opt/test"
        assert result["API_KEY"] == "test-key-123"
        assert result["API_TIMEOUT"] == "30"

    def test_comments_and_blanks_skipped(self, env_file: Path) -> None:
        result = _load_env_file(env_file)
        # Should not contain comment lines
        assert "#" not in "".join(result.keys())

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = _load_env_file(tmp_path / "nonexistent.env")
        assert result == {}

    def test_quotes_stripped(self, env_file: Path) -> None:
        result = _load_env_file(env_file)
        assert result["BASE_DIR"] == "/opt/test"
        assert not result["BASE_DIR"].startswith('"')


class TestLoadIni:
    def test_basic_parsing(self, ini_file: Path) -> None:
        result = _load_ini(ini_file)
        assert "production" in result
        assert result["production"]["host"] == "localhost"
        assert result["production"]["port"] == "5432"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = _load_ini(tmp_path / "nonexistent.conf")
        assert result.sections() == []


class TestLoadConfig:
    def test_returns_pipeline_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Create minimal config files so load_config can find them
        env_file = tmp_path / "pipeline.env"
        env_file.write_text(
            f'BASE_DIR="{tmp_path}"\n'
            f'LOG_DIR="{tmp_path / "logs"}"\n'
        )
        cfg = load_config(tmp_path)
        assert isinstance(cfg, PipelineConfig)

    def test_env_vars_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        env_file = tmp_path / "pipeline.env"
        env_file.write_text('API_KEY="from-file"\n')
        monkeypatch.setenv("API_KEY", "from-env")
        cfg = load_config(tmp_path)
        assert cfg.api.api_key == "from-env"
