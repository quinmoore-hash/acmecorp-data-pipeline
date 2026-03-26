"""Tests for acmecorp_pipeline.lock_manager module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acmecorp_pipeline.lock_manager import Lock, _pid_alive


class TestPidAlive:
    def test_current_process(self) -> None:
        assert _pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self) -> None:
        assert _pid_alive(9999999) is False


class TestLock:
    def test_acquire_release(self, tmp_path: Path) -> None:
        lock = Lock("test_job", lock_dir=tmp_path)
        assert lock.acquire() is True
        assert lock.lockfile.is_file()
        lock.release()
        assert not lock.lockfile.is_file()

    def test_context_manager(self, tmp_path: Path) -> None:
        with Lock("test_ctx", lock_dir=tmp_path) as lock:
            assert lock.lockfile.is_file()
        assert not lock.lockfile.is_file()

    def test_stale_lock_cleaned(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "stale.lock"
        lockfile.write_text("9999999")  # Non-existent PID

        lock = Lock("stale", lock_dir=tmp_path)
        assert lock.acquire(timeout=10) is True
        lock.release()

    def test_active_lock_blocks(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "active.lock"
        lockfile.write_text(str(os.getpid()))  # Current PID = active

        lock = Lock("active", timeout=1, lock_dir=tmp_path)
        assert lock.acquire(timeout=1) is False

    def test_context_manager_raises_on_fail(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "blocked.lock"
        lockfile.write_text(str(os.getpid()))

        with pytest.raises(RuntimeError, match="Could not acquire lock"):
            with Lock("blocked", timeout=1, lock_dir=tmp_path):
                pass
