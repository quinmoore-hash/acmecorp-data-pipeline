"""System health check — validates database, disk, services, and processes.

Replaces ``health_check.sh``.  Runs every 5 minutes via cron.
"""

from __future__ import annotations

import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.db_helpers import check_db_connection
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert

log = get_logger("health_check")

HEALTH_FILE = Path("/tmp/acmecorp_health_status")
COOLDOWN_FILE = Path("/tmp/acmecorp_hc_cooldown")
COOLDOWN_SECONDS = 300  # 5 min cooldown between repeated alerts


def _check_tcp(host: str, port: int, timeout: float = 5.0) -> bool:
    """Return ``True`` if a TCP connection can be established."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _check_disk(path: str | Path, warn_pct: int = 80, crit_pct: int = 95) -> tuple[str, int]:
    """Check disk usage at *path*.  Returns (status, pct_used)."""
    try:
        usage = shutil.disk_usage(str(path))
        pct = int(usage.used * 100 / usage.total)
    except OSError:
        return "UNKNOWN", 0
    if pct >= crit_pct:
        return "CRITICAL", pct
    if pct >= warn_pct:
        return "WARNING", pct
    return "OK", pct


def _should_alert(key: str) -> bool:
    """Return ``True`` if we should alert (cooldown expired)."""
    cooldowns: dict[str, float] = {}
    if COOLDOWN_FILE.is_file():
        for line in COOLDOWN_FILE.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    cooldowns[k] = float(v)
                except ValueError:
                    pass
    now = time.time()
    last = cooldowns.get(key, 0)
    if now - last < COOLDOWN_SECONDS:
        return False
    cooldowns[key] = now
    COOLDOWN_FILE.write_text("\n".join(f"{k}={v}" for k, v in cooldowns.items()) + "\n")
    return True


def run_health_check(config: PipelineConfig) -> int:
    """Execute all health checks.

    Returns:
        0 = all OK, 1 = critical issues, 2 = warnings only.
    """
    results: list[tuple[str, str, str]] = []  # (check_name, status, detail)
    critical = False
    warning = False

    # 1. Database connectivity
    for profile_name in config.db_profiles:
        if check_db_connection(config, profile_name):
            results.append((f"db:{profile_name}", "OK", "Connected"))
        else:
            results.append((f"db:{profile_name}", "CRITICAL", "Connection failed"))
            critical = True
            if _should_alert(f"hc_db_{profile_name}"):
                alert(f"Health check: DB '{profile_name}' unreachable", "CRITICAL", config)

    # 2. Disk usage
    for label, path in [
        ("data", config.paths.base_dir),
        ("logs", config.paths.log_dir),
    ]:
        status, pct = _check_disk(path)
        results.append((f"disk:{label}", status, f"{pct}% used"))
        if status == "CRITICAL":
            critical = True
            if _should_alert(f"hc_disk_{label}"):
                alert(f"Health check: disk '{label}' at {pct}%", "CRITICAL", config)
        elif status == "WARNING":
            warning = True
            if _should_alert(f"hc_disk_{label}"):
                alert(f"Health check: disk '{label}' at {pct}%", "WARNING", config)

    # 3. External connectivity (S3 endpoint)
    s3_reachable = _check_tcp("s3.amazonaws.com", 443)
    results.append(("net:s3", "OK" if s3_reachable else "WARNING", "reachable" if s3_reachable else "unreachable"))
    if not s3_reachable:
        warning = True

    # 4. Lock files — check for stale locks
    lock_dir = Path("/tmp/acmecorp_locks")
    if lock_dir.is_dir():
        import os
        for lock_file in lock_dir.glob("*.lock"):
            try:
                pid = int(lock_file.read_text().strip())
                try:
                    os.kill(pid, 0)
                    results.append((f"lock:{lock_file.stem}", "OK", f"pid={pid} running"))
                except ProcessLookupError:
                    results.append((f"lock:{lock_file.stem}", "WARNING", f"stale lock (pid={pid})"))
                    warning = True
                except PermissionError:
                    results.append((f"lock:{lock_file.stem}", "OK", f"pid={pid} running (different user)"))
            except (ValueError, OSError):
                results.append((f"lock:{lock_file.stem}", "WARNING", "unreadable lock file"))
                warning = True

    # 5. Log directory writable
    try:
        test_file = config.paths.log_dir / ".health_check_test"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(("log_dir:writable", "OK", "writable"))
    except OSError:
        results.append(("log_dir:writable", "WARNING", "not writable"))
        warning = True

    # Write status file
    lines = [f"timestamp={datetime.now():%Y-%m-%d %H:%M:%S}"]
    for name, status, detail in results:
        lines.append(f"{name}={status} ({detail})")

    overall = "CRITICAL" if critical else ("WARNING" if warning else "OK")
    lines.append(f"overall={overall}")
    HEALTH_FILE.write_text("\n".join(lines) + "\n")

    log.info("Health check: %s (%d checks)", overall, len(results))
    for name, status, detail in results:
        if status != "OK":
            log.warning("  %s: %s — %s", name, status, detail)

    if critical:
        return 1
    if warning:
        return 2
    return 0


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)
    exit_code = run_health_check(cfg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
