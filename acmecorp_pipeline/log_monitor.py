"""Log monitor and alerting — scans pipeline logs for errors and anomalies.

Replaces ``log_monitor.sh``.  Runs every 15 minutes via cron.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert

log = get_logger("log_monitor")

STATE_FILE = Path("/tmp/acmecorp_log_monitor_state")
COOLDOWN_FILE = Path("/tmp/acmecorp_alert_cooldown")
ALERT_COOLDOWN = 900  # 15 minutes

ERROR_PATTERNS = [
    "FATAL",
    "CRITICAL",
    "OOM",
    "out of memory",
    "disk full",
    "connection refused",
    "permission denied",
    "segmentation fault",
    "killed",
    "no space left on device",
]


def _load_state() -> dict[str, int]:
    """Load last-scanned byte positions from state file."""
    state: dict[str, int] = {}
    if STATE_FILE.is_file():
        for line in STATE_FILE.read_text().splitlines():
            if ":" in line:
                path, pos = line.rsplit(":", 1)
                try:
                    state[path] = int(pos)
                except ValueError:
                    pass
    return state


def _save_state(state: dict[str, int]) -> None:
    """Persist byte positions to state file."""
    lines = [f"{path}:{pos}" for path, pos in state.items()]
    STATE_FILE.write_text("\n".join(lines) + "\n")


def _load_cooldowns() -> dict[str, float]:
    """Load alert cooldown timestamps."""
    cooldowns: dict[str, float] = {}
    if COOLDOWN_FILE.is_file():
        for line in COOLDOWN_FILE.read_text().splitlines():
            if ":" in line:
                key, ts = line.rsplit(":", 1)
                try:
                    cooldowns[key] = float(ts)
                except ValueError:
                    pass
    return cooldowns


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    """Persist cooldown timestamps."""
    lines = [f"{key}:{ts}" for key, ts in cooldowns.items()]
    COOLDOWN_FILE.write_text("\n".join(lines) + "\n")


def _check_cooldown(key: str, cooldowns: dict[str, float]) -> bool:
    """Return ``True`` if we should send an alert (not in cooldown)."""
    last = cooldowns.get(key, 0)
    return (time.time() - last) >= ALERT_COOLDOWN


def _scan_log_file(
    logfile: Path,
    state: dict[str, int],
    cooldowns: dict[str, float],
    config: PipelineConfig,
) -> None:
    """Scan a single log file for error patterns."""
    key = str(logfile)
    last_pos = state.get(key, 0)

    try:
        current_size = logfile.stat().st_size
    except OSError:
        return

    # File was rotated — start from beginning
    if current_size < last_pos:
        last_pos = 0

    if current_size <= last_pos:
        return

    try:
        with open(logfile, "rb") as fh:
            fh.seek(last_pos)
            new_content = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return

    errors_found = 0
    error_samples: list[str] = []
    for pattern in ERROR_PATTERNS:
        matches = [
            line for line in new_content.splitlines()
            if re.search(pattern, line, re.IGNORECASE)
        ]
        if matches:
            errors_found += 1
            error_samples.extend(matches[:5])

    error_count = new_content.count("[ERROR]")
    warn_count = new_content.count("[WARN]")

    if errors_found > 0 or error_count > 10:
        alert_key = f"logmon_{logfile.name}"
        if _check_cooldown(alert_key, cooldowns):
            sample_text = "\n".join(error_samples[:10])
            alert(
                f"Log Monitor: {logfile.name} - {error_count} errors, {warn_count} warnings detected.\n"
                f"Sample:\n{sample_text}",
                "WARNING",
                config,
            )
            cooldowns[alert_key] = time.time()

    state[key] = current_size


def run_log_monitor(config: PipelineConfig) -> None:
    """Scan all log files and check disk usage."""
    state = _load_state()
    cooldowns = _load_cooldowns()

    log_dir = config.paths.log_dir
    log.debug("Scanning logs in %s...", log_dir)

    if log_dir.is_dir():
        for logfile in log_dir.glob("*.log"):
            if logfile.is_file():
                _scan_log_file(logfile, state, cooldowns, config)

    # Check disk usage
    try:
        usage = shutil.disk_usage(str(log_dir))
        pct_used = int(usage.used * 100 / usage.total)
    except OSError:
        pct_used = 0

    if pct_used > 80:
        if _check_cooldown("disk_usage", cooldowns):
            alert(f"Disk usage on log volume: {pct_used}%", "WARNING", config)
            cooldowns["disk_usage"] = time.time()

    if pct_used > 95:
        if _check_cooldown("disk_critical", cooldowns):
            alert(f"CRITICAL: Disk usage at {pct_used}%! Pipeline may fail!", "CRITICAL", config)
            cooldowns["disk_critical"] = time.time()

    _save_state(state)
    _save_cooldowns(cooldowns)
    log.debug("Log monitor scan complete")


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)
    run_log_monitor(cfg)


if __name__ == "__main__":
    main()
