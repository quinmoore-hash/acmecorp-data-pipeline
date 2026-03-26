"""Dependency checker — validates that all required tools and services
are available before running the pipeline.

Replaces ``check_dependencies.sh``.  Checks Python packages, external
commands, database connectivity, network endpoints, and disk space.
"""

from __future__ import annotations

import importlib
import shutil
import socket
import sys

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import check_db_connection
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("check_dependencies")

# Python packages required at runtime
REQUIRED_PACKAGES = [
    "psycopg2",
    "requests",
    "boto3",
    "dateutil",
]

# External CLI tools that may be needed
OPTIONAL_CLI_TOOLS = [
    "pg_dump",
    "pg_restore",
    "aws",
]

# Network endpoints to verify
NETWORK_CHECKS = [
    ("s3.amazonaws.com", 443),
    ("api.vendor-a.com", 443),
]


def check_python_packages() -> list[str]:
    """Return a list of missing required Python packages."""
    missing: list[str] = []
    for package in REQUIRED_PACKAGES:
        try:
            importlib.import_module(package)
        except ImportError:
            missing.append(package)
    return missing


def check_cli_tools() -> dict[str, bool]:
    """Check availability of external CLI tools."""
    results: dict[str, bool] = {}
    for tool in OPTIONAL_CLI_TOOLS:
        results[tool] = shutil.which(tool) is not None
    return results


def check_network(host: str, port: int, timeout: float = 5.0) -> bool:
    """Return ``True`` if *host*:*port* is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def check_directories(config: PipelineConfig) -> list[str]:
    """Return a list of directories that don't exist or aren't writable."""
    issues: list[str] = []
    dirs = [
        config.paths.input_dir,
        config.paths.processed_dir,
        config.paths.archive_dir,
        config.paths.staging_dir,
        config.paths.error_dir,
        config.paths.log_dir,
    ]
    for d in dirs:
        if not d.is_dir():
            issues.append(f"Missing directory: {d}")
        elif not (d.stat().st_mode & 0o200):
            issues.append(f"Not writable: {d}")
    return issues


def check_disk_space(config: PipelineConfig, min_gb: float = 5.0) -> list[str]:
    """Check that critical paths have enough free disk space."""
    issues: list[str] = []
    for label, path in [("data", config.paths.base_dir), ("logs", config.paths.log_dir)]:
        try:
            usage = shutil.disk_usage(str(path))
            free_gb = usage.free / (1024**3)
            if free_gb < min_gb:
                issues.append(f"Low disk space on {label}: {free_gb:.1f} GB free (min {min_gb} GB)")
        except OSError:
            issues.append(f"Cannot check disk for {label}: {path}")
    return issues


def run_dependency_check(config: PipelineConfig) -> int:
    """Run all dependency checks.

    Returns:
        0 if all critical checks pass, 1 otherwise.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Python packages
    missing = check_python_packages()
    if missing:
        errors.append(f"Missing Python packages: {', '.join(missing)}")
    else:
        log.info("All required Python packages available")

    # 2. CLI tools
    tools = check_cli_tools()
    for tool, available in tools.items():
        if available:
            log.info("CLI tool available: %s", tool)
        else:
            warnings.append(f"Optional CLI tool not found: {tool}")

    # 3. Directories
    dir_issues = check_directories(config)
    for issue in dir_issues:
        warnings.append(issue)
    if not dir_issues:
        log.info("All directories OK")

    # 4. Disk space
    disk_issues = check_disk_space(config)
    for issue in disk_issues:
        warnings.append(issue)
    if not disk_issues:
        log.info("Disk space OK")

    # 5. Database connectivity
    for profile_name in config.db_profiles:
        if check_db_connection(config, profile_name):
            log.info("Database '%s' connectivity OK", profile_name)
        else:
            errors.append(f"Cannot connect to database: {profile_name}")

    # 6. Network
    for host, port in NETWORK_CHECKS:
        if check_network(host, port):
            log.info("Network endpoint reachable: %s:%d", host, port)
        else:
            warnings.append(f"Network endpoint unreachable: {host}:{port}")

    # Summary
    print("\n=== Dependency Check Summary ===")
    if errors:
        for e in errors:
            print(f"  [ERROR]   {e}")
    if warnings:
        for w in warnings:
            print(f"  [WARNING] {w}")
    if not errors and not warnings:
        print("  All checks passed!")

    print(f"\n  Errors:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")

    return 1 if errors else 0


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)
    exit_code = run_dependency_check(cfg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
