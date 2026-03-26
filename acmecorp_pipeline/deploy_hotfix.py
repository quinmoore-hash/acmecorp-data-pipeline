"""Deploy hotfix — controlled deployment of pipeline changes.

Replaces ``deploy_hotfix.sh``.  Validates, backs up, and deploys
updated scripts with rollback capability.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.lock_manager import Lock
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert, send_slack

log = get_logger("deploy_hotfix")

DEPLOY_DIR = Path("/opt/acmecorp/pipeline")
BACKUP_DIR = Path("/opt/acmecorp/backups/deploy")
ROLLBACK_MANIFEST = DEPLOY_DIR / ".rollback_manifest"


def _create_backup(deploy_dir: Path, backup_dir: Path) -> Path:
    """Create a timestamped backup of the current deployment."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"deploy_backup_{timestamp}"
    backup_path.mkdir(parents=True, exist_ok=True)

    if deploy_dir.is_dir():
        for item in deploy_dir.iterdir():
            if item.name.startswith("."):
                continue
            dest = backup_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    log.info("Backup created: %s", backup_path)
    return backup_path


def _run_pre_deploy_checks(config: PipelineConfig) -> bool:
    """Run pre-deployment validation checks."""
    log.info("Running pre-deploy checks...")

    # Check Python syntax of all modules
    import py_compile
    package_dir = Path(__file__).resolve().parent
    for py_file in package_dir.glob("*.py"):
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as exc:
            log.error("Syntax error in %s: %s", py_file.name, exc)
            return False

    log.info("Pre-deploy checks passed")
    return True


def deploy_hotfix(
    config: PipelineConfig,
    source_dir: Path | None = None,
    skip_backup: bool = False,
    dry_run: bool = False,
) -> bool:
    """Deploy pipeline changes.

    Args:
        config: Pipeline configuration.
        source_dir: Directory containing updated code.  Defaults to the
            package directory.
        skip_backup: Skip backup creation.
        dry_run: Validate without deploying.

    Returns:
        ``True`` on success.
    """
    if source_dir is None:
        source_dir = Path(__file__).resolve().parent

    log.info("Starting deployment from: %s", source_dir)
    send_slack("Pipeline deployment starting...", "INFO", config.slack)

    with Lock("deploy", timeout=30):
        # Pre-deploy checks
        if not _run_pre_deploy_checks(config):
            log.error("Pre-deploy checks failed, aborting")
            alert("Deployment aborted: pre-deploy checks failed", "CRITICAL", config)
            return False

        if dry_run:
            log.info("DRY RUN: validation passed, no changes made")
            return True

        # Create backup
        backup_path: Path | None = None
        if not skip_backup:
            backup_path = _create_backup(DEPLOY_DIR, BACKUP_DIR)
            # Write rollback manifest
            ROLLBACK_MANIFEST.write_text(str(backup_path))

        # Deploy
        log.info("Deploying to %s...", DEPLOY_DIR)
        DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

        for item in source_dir.iterdir():
            if item.name.startswith((".", "__pycache__")):
                continue
            dest = DEPLOY_DIR / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        log.info("Deployment complete")
        send_slack(
            f"Pipeline deployment complete. Backup: {backup_path or 'skipped'}",
            "INFO",
            config.slack,
        )
        return True


def rollback(config: PipelineConfig) -> bool:
    """Rollback to the previous deployment using the manifest."""
    if not ROLLBACK_MANIFEST.is_file():
        log.error("No rollback manifest found")
        return False

    backup_path = Path(ROLLBACK_MANIFEST.read_text().strip())
    if not backup_path.is_dir():
        log.error("Backup directory not found: %s", backup_path)
        return False

    log.info("Rolling back to: %s", backup_path)
    send_slack(f"Pipeline ROLLBACK initiated from {backup_path}", "WARNING", config.slack)

    with Lock("deploy", timeout=30):
        # Remove current deployment
        if DEPLOY_DIR.is_dir():
            for item in DEPLOY_DIR.iterdir():
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Restore from backup
        for item in backup_path.iterdir():
            dest = DEPLOY_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        log.info("Rollback complete")
        send_slack("Pipeline rollback complete", "INFO", config.slack)
        return True


def main() -> None:
    """CLI entry point matching original ``deploy_hotfix.sh`` interface.

    Usage:
        deploy_hotfix.py deploy [--dry-run] [--skip-backup] [source_dir]
        deploy_hotfix.py rollback
    """
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} {{deploy|rollback}} [options]")
        sys.exit(1)

    action = sys.argv[1]

    if action == "deploy":
        dry_run = "--dry-run" in sys.argv
        skip_backup = "--skip-backup" in sys.argv
        source_dir = None
        for arg in sys.argv[2:]:
            if not arg.startswith("-"):
                source_dir = Path(arg)
                break
        ok = deploy_hotfix(cfg, source_dir, skip_backup, dry_run)
        sys.exit(0 if ok else 1)
    elif action == "rollback":
        ok = rollback(cfg)
        sys.exit(0 if ok else 1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
