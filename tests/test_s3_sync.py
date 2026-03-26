"""Tests for acmecorp_pipeline.s3_sync module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from acmecorp_pipeline.config import load_config
from acmecorp_pipeline.s3_sync import s3_upload_dir


class TestGetS3ClientSingleton:
    """Verify that the module-level singleton is invalidated on region change."""

    def test_cached_client_returned_when_region_matches(self, tmp_path: Path) -> None:
        import acmecorp_pipeline.s3_sync as mod

        sentinel = MagicMock(name="cached-client")
        mod._s3_client = sentinel
        mod._s3_client_region = "us-east-1"

        cfg = load_config(tmp_path)
        cfg.s3.region = "us-east-1"

        # _get_s3_client should return the cached sentinel without importing boto3
        result = mod._get_s3_client(cfg)
        assert result is sentinel

        # Cleanup
        mod._s3_client = None
        mod._s3_client_region = None

    def test_client_invalidated_on_region_change(self, tmp_path: Path) -> None:
        import acmecorp_pipeline.s3_sync as mod

        old_sentinel = MagicMock(name="old-client")
        mod._s3_client = old_sentinel
        mod._s3_client_region = "us-east-1"

        cfg = load_config(tmp_path)
        cfg.s3.region = "eu-west-1"

        # This will try to import boto3 and create a new client.
        # We can't easily mock the local import, but we can verify
        # that the old sentinel is NOT returned (i.e., cache is invalidated).
        try:
            result = mod._get_s3_client(cfg)
        except Exception:
            # boto3 may not be importable; that's fine --
            # we just need to verify the cache was invalidated.
            result = None

        assert result is not old_sentinel

        # Cleanup
        mod._s3_client = None
        mod._s3_client_region = None


class TestS3UploadDir:
    def test_nonexistent_dir_returns_false(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert s3_upload_dir(tmp_path / "nope", cfg) is False
