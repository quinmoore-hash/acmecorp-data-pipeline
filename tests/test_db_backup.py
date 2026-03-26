"""Tests for acmecorp_pipeline.db_backup module."""

from __future__ import annotations

import time
from pathlib import Path

from acmecorp_pipeline.db_backup import _cleanup_old_backups


class TestCleanupOldBackups:
    def test_removes_old_dirs(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old_backup"
        old_dir.mkdir()
        (old_dir / "dump.sql").write_text("data")
        # Set mtime to 100 days ago
        import os
        old_ts = time.time() - 100 * 86400
        os.utime(old_dir, (old_ts, old_ts))

        new_dir = tmp_path / "new_backup"
        new_dir.mkdir()
        (new_dir / "dump.sql").write_text("data")

        _cleanup_old_backups(tmp_path, retention_days=30)

        assert not old_dir.exists()
        assert new_dir.exists()

    def test_nonexistent_base_dir(self, tmp_path: Path) -> None:
        # Should not raise
        _cleanup_old_backups(tmp_path / "nope", retention_days=30)

    def test_keeps_recent_dirs(self, tmp_path: Path) -> None:
        recent = tmp_path / "recent"
        recent.mkdir()
        (recent / "dump.sql").write_text("data")

        _cleanup_old_backups(tmp_path, retention_days=30)
        assert recent.exists()
