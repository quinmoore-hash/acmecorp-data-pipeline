"""S3 synchronisation — upload/download files to the AcmeCorp data lake.

Replaces ``s3_sync.sh``.  Uses :mod:`boto3` instead of the ``aws`` CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("s3_sync")

# Lazy-loaded boto3 client; invalidated when the configured region changes.
_s3_client = None
_s3_client_region: str | None = None


def _get_s3_client(config: PipelineConfig):
    """Return a cached boto3 S3 client, re-creating it if the region changed."""
    global _s3_client, _s3_client_region
    if _s3_client is None or _s3_client_region != config.s3.region:
        import boto3
        _s3_client = boto3.client(
            "s3",
            region_name=config.s3.region,
        )
        _s3_client_region = config.s3.region
    return _s3_client


def s3_upload_file(
    local_path: Path,
    config: PipelineConfig,
    s3_key: Optional[str] = None,
) -> bool:
    """Upload a single file to S3.

    Args:
        local_path: Local file path.
        config: Pipeline configuration.
        s3_key: S3 object key.  Defaults to the filename.

    Returns:
        ``True`` on success.
    """
    if s3_key is None:
        s3_key = local_path.name

    bucket = config.s3.bucket
    log.info("Uploading %s -> s3://%s/%s", local_path, bucket, s3_key)

    try:
        client = _get_s3_client(config)
        client.upload_file(str(local_path), bucket, s3_key)
        return True
    except Exception as exc:
        log.error("S3 upload failed: %s", exc)
        return False


def s3_upload_dir(
    local_dir: Path,
    config: PipelineConfig,
    s3_prefix: str = "",
) -> bool:
    """Upload all files in a directory to S3.

    Args:
        local_dir: Local directory path.
        config: Pipeline configuration.
        s3_prefix: S3 key prefix for all files.

    Returns:
        ``True`` if all files uploaded successfully.
    """
    if not local_dir.is_dir():
        log.error("Directory not found: %s", local_dir)
        return False

    success = True
    for filepath in local_dir.rglob("*"):
        if not filepath.is_file():
            continue
        relative = filepath.relative_to(local_dir)
        s3_key = f"{s3_prefix}{relative}".replace("\\", "/")
        if not s3_upload_file(filepath, config, s3_key):
            success = False

    return success


def s3_download_file(
    s3_key: str,
    local_path: Path,
    config: PipelineConfig,
) -> bool:
    """Download a single file from S3.

    Args:
        s3_key: S3 object key.
        local_path: Local destination path.
        config: Pipeline configuration.

    Returns:
        ``True`` on success.
    """
    bucket = config.s3.bucket
    log.info("Downloading s3://%s/%s -> %s", bucket, s3_key, local_path)

    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        client = _get_s3_client(config)
        client.download_file(bucket, s3_key, str(local_path))
        return True
    except Exception as exc:
        log.error("S3 download failed: %s", exc)
        return False


def s3_sync_incoming(config: PipelineConfig) -> int:
    """Sync the ``incoming/`` prefix from S3 to the local input directory.

    Returns the number of files downloaded.
    """
    bucket = config.s3.bucket
    prefix = "incoming/"
    input_dir = config.paths.input_dir
    input_dir.mkdir(parents=True, exist_ok=True)

    log.info("Syncing s3://%s/%s -> %s", bucket, prefix, input_dir)

    try:
        client = _get_s3_client(config)
        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    except Exception as exc:
        log.error("S3 list failed: %s", exc)
        return -1

    downloaded = 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            filename = key[len(prefix):]
            local_path = input_dir / filename

            if local_path.exists() and local_path.stat().st_size == obj["Size"]:
                continue  # Already downloaded

            if s3_download_file(key, local_path, config):
                downloaded += 1

    log.info("S3 sync complete: %d new files", downloaded)
    return downloaded


def s3_archive(config: PipelineConfig) -> int:
    """Upload local archive directory to S3 ``archive/`` prefix.

    Returns the number of files uploaded.
    """
    archive_dir = config.paths.archive_dir
    if not archive_dir.is_dir():
        return 0

    uploaded = 0

    for filepath in archive_dir.iterdir():
        if not filepath.is_file():
            continue
        s3_key = f"archive/{filepath.name}"
        if s3_upload_file(filepath, config, s3_key):
            uploaded += 1

    log.info("Archived %d files to S3", uploaded)
    return uploaded


def main() -> None:
    """CLI entry point matching original ``s3_sync.sh`` interface.

    Usage:
        s3_sync.py sync-incoming
        s3_sync.py archive
        s3_sync.py upload <local_file> [s3_key]
        s3_sync.py download <s3_key> <local_file>
    """
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} {{sync-incoming|archive|upload|download}} [args...]")
        sys.exit(1)

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    action = sys.argv[1]

    if action == "sync-incoming":
        result = s3_sync_incoming(cfg)
        sys.exit(0 if result >= 0 else 1)
    elif action == "archive":
        s3_archive(cfg)
    elif action == "upload" and len(sys.argv) >= 3:
        local_file = Path(sys.argv[2])
        s3_key = sys.argv[3] if len(sys.argv) > 3 else None
        ok = s3_upload_file(local_file, cfg, s3_key)
        sys.exit(0 if ok else 1)
    elif action == "download" and len(sys.argv) >= 4:
        ok = s3_download_file(sys.argv[2], Path(sys.argv[3]), cfg)
        sys.exit(0 if ok else 1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
