"""File-based lock manager to prevent concurrent job runs.

Replaces ``lock_manager.sh``.  Improvements over the original:

- Uses :func:`os.kill` with signal 0 to validate stale PIDs
- Context-manager interface for automatic cleanup
- No risk of leaving stale locks on normal exit
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import Optional

from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("lock_manager")

LOCK_DIR = Path("/tmp/acmecorp_locks")


class Lock:
    """A file-based lock for a named job.

    Usage::

        lock = Lock("etl_master")
        if lock.acquire(timeout=300):
            try:
                ...
            finally:
                lock.release()

    Or as a context manager::

        with Lock("etl_master", timeout=300):
            ...
    """

    def __init__(self, job_name: str, timeout: int = 60, lock_dir: Optional[Path] = None):
        self.job_name = job_name
        self.timeout = timeout
        self.lock_dir = lock_dir or LOCK_DIR
        self.lockfile = self.lock_dir / f"{job_name}.lock"
        self._acquired = False

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def acquire(self, timeout: Optional[int] = None) -> bool:
        """Try to acquire the lock, waiting up to *timeout* seconds.

        Returns ``True`` if the lock was acquired.
        """
        timeout = timeout if timeout is not None else self.timeout
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        elapsed = 0
        while self.lockfile.exists():
            # Check for stale lock
            try:
                lock_pid = int(self.lockfile.read_text().strip())
            except (ValueError, OSError):
                lock_pid = None

            if lock_pid is not None and not _pid_alive(lock_pid):
                log.warning(
                    "Removing stale lock for %s (pid=%s)", self.job_name, lock_pid
                )
                self._remove_lockfile()
                break

            time.sleep(5)
            elapsed += 5
            if elapsed >= timeout:
                log.error("Timeout acquiring lock for %s", self.job_name)
                return False

        self.lockfile.write_text(str(os.getpid()))
        self._acquired = True
        return True

    def release(self) -> None:
        """Release the lock by removing the lockfile."""
        self._remove_lockfile()
        self._acquired = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Lock":
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock for {self.job_name}")
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _remove_lockfile(self) -> None:
        try:
            self.lockfile.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    """Return ``True`` if a process with *pid* is running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
