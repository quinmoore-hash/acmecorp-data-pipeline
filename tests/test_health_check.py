"""Tests for acmecorp_pipeline.health_check module."""

from __future__ import annotations

from acmecorp_pipeline.health_check import _check_disk, _check_tcp


class TestCheckTcp:
    def test_localhost_unreachable_port(self) -> None:
        # Port 1 is almost certainly not listening
        assert _check_tcp("127.0.0.1", 1, timeout=0.5) is False

    def test_bad_host(self) -> None:
        assert _check_tcp("192.0.2.1", 80, timeout=0.5) is False


class TestCheckDisk:
    def test_root_returns_ok_or_warning(self) -> None:
        status, pct = _check_disk("/")
        assert status in ("OK", "WARNING", "CRITICAL", "UNKNOWN")
        assert 0 <= pct <= 100

    def test_nonexistent_path(self) -> None:
        status, pct = _check_disk("/nonexistent/path/xyz")
        assert status == "UNKNOWN"
        assert pct == 0

    def test_critical_threshold(self) -> None:
        # With crit_pct=0, everything is critical
        status, _pct = _check_disk("/", warn_pct=0, crit_pct=0)
        assert status == "CRITICAL"
