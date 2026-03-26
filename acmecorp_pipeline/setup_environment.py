"""Environment setup — creates directories, validates configuration,
and installs dependencies for the AcmeCorp data pipeline.

Replaces ``setup_environment.sh``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("setup_environment")


def _ensure_dirs(config: PipelineConfig) -> None:
    """Create all required directories."""
    dirs = [
        config.paths.input_dir,
        config.paths.processed_dir,
        config.paths.archive_dir,
        config.paths.staging_dir,
        config.paths.error_dir,
        config.paths.log_dir,
        config.paths.base_dir / "reports",
        Path("/opt/acmecorp/backups/database/full"),
        Path("/opt/acmecorp/backups/database/incremental"),
        Path("/opt/acmecorp/backups/deploy"),
        Path("/tmp/acmecorp_locks"),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        log.info("Directory OK: %s", d)


def _check_python_deps() -> list[str]:
    """Check that required Python packages are importable."""
    required = {
        "psycopg2": "psycopg2-binary",
        "requests": "requests",
        "boto3": "boto3",
        "dateutil": "python-dateutil",
    }
    missing: list[str] = []
    for module, pip_name in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pip_name)
    return missing


def _install_python_deps(packages: list[str]) -> bool:
    """Install missing Python packages via pip."""
    if not packages:
        return True
    log.info("Installing missing packages: %s", ", ".join(packages))
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", *packages],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("pip install failed: %s", result.stderr)
        return False
    return True


def setup_environment(config: PipelineConfig, install_deps: bool = True) -> bool:
    """Set up the pipeline environment.

    1. Create required directories
    2. Install missing Python dependencies
    3. Validate configuration
    4. Print environment summary

    Returns ``True`` on success.
    """
    print("=" * 60)
    print("AcmeCorp Data Pipeline — Environment Setup")
    print("=" * 60)

    # 1. Directories
    print("\n--- Creating directories ---")
    _ensure_dirs(config)

    # 2. Python dependencies
    print("\n--- Checking Python dependencies ---")
    missing = _check_python_deps()
    if missing:
        if install_deps:
            if not _install_python_deps(missing):
                print(f"ERROR: Failed to install: {', '.join(missing)}")
                return False
            print("Dependencies installed successfully")
        else:
            print(f"Missing packages (install manually): {', '.join(missing)}")
    else:
        print("All required packages available")

    # 3. Configuration validation
    print("\n--- Validating configuration ---")
    issues: list[str] = []

    if not config.db_profiles:
        issues.append("No database profiles configured")

    if not config.api.api_key:
        issues.append("API key not configured (set API_KEY env var)")

    if not config.s3.bucket:
        issues.append("S3 bucket not configured")

    if issues:
        for issue in issues:
            print(f"  WARNING: {issue}")
    else:
        print("  Configuration OK")

    # 4. Summary
    print("\n--- Environment Summary ---")
    print(f"  Python:      {sys.executable} ({sys.version.split()[0]})")
    print(f"  Base dir:    {config.paths.base_dir}")
    print(f"  Log dir:     {config.paths.log_dir}")
    print(f"  Input dir:   {config.paths.input_dir}")
    print(f"  S3 bucket:   {config.s3.bucket}")
    print(f"  DB profiles: {', '.join(config.db_profiles.keys())}")
    print(f"  Log level:   {config.log_cfg.log_level}")

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)

    return True


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    install = "--no-install" not in sys.argv
    ok = setup_environment(cfg, install_deps=install)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
