"""Access-log parser — extracts metrics from web/API access logs.

Replaces ``parse_access_logs.sh``.  Uses Python :mod:`re` instead of
``awk``/``grep`` pipelines.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import load_config
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("parse_access_logs")

# Common combined log format
# 127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326
_LOG_RE = re.compile(
    r'^(?P<ip>\S+)\s+'         # IP
    r'\S+\s+'                  # ident (usually -)
    r'(?P<user>\S+)\s+'        # user
    r'\[(?P<time>[^\]]+)\]\s+' # timestamp
    r'"(?P<method>\S+)\s+'     # method
    r'(?P<path>\S+)\s+'        # path
    r'\S+"\s+'                 # protocol
    r'(?P<status>\d+)\s+'      # status code
    r'(?P<size>\S+)'           # response size
)


def parse_access_logs(
    input_files: list[Path],
    output_file: Path,
    summary_file: Optional[Path] = None,
) -> bool:
    """Parse access log files and produce a CSV and optional summary.

    Args:
        input_files: List of access log file paths.
        output_file: Path for the parsed CSV output.
        summary_file: Optional path for the summary report.

    Returns:
        ``True`` on success.
    """
    total_lines = 0
    parsed_lines = 0

    status_counter: Counter[str] = Counter()
    path_counter: Counter[str] = Counter()
    ip_counter: Counter[str] = Counter()
    error_paths: Counter[str] = Counter()
    hourly: Counter[str] = Counter()
    total_bytes = 0

    rows: list[dict[str, str]] = []

    for logfile in input_files:
        if not logfile.is_file():
            log.warning("Skipping missing file: %s", logfile)
            continue

        log.info("Parsing: %s", logfile.name)

        with open(logfile, errors="replace") as fh:
            for line in fh:
                total_lines += 1
                m = _LOG_RE.match(line)
                if not m:
                    continue

                parsed_lines += 1
                ip = m.group("ip")
                method = m.group("method")
                path = m.group("path")
                status = m.group("status")
                size_str = m.group("size")
                time_str = m.group("time")

                size = int(size_str) if size_str != "-" else 0
                total_bytes += size

                status_counter[status] += 1
                path_counter[path] += 1
                ip_counter[ip] += 1

                if status.startswith(("4", "5")):
                    error_paths[f"{status} {path}"] += 1

                # Extract hour
                try:
                    dt = datetime.strptime(time_str.split()[0], "%d/%b/%Y:%H:%M:%S")
                    hourly[f"{dt:%H}"] += 1
                except ValueError:
                    pass

                rows.append({
                    "ip": ip,
                    "timestamp": time_str,
                    "method": method,
                    "path": path,
                    "status": status,
                    "size": str(size),
                })

    # Write parsed CSV
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ip", "timestamp", "method", "path", "status", "size"]
    with open(output_file, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info("Parsed %d/%d lines -> %s", parsed_lines, total_lines, output_file)

    # Write summary
    if summary_file:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "========================================",
            f"Access Log Summary — {datetime.now():%Y-%m-%d %H:%M:%S}",
            "========================================",
            f"Total lines:   {total_lines}",
            f"Parsed lines:  {parsed_lines}",
            f"Total bytes:   {total_bytes:,}",
            "",
            "--- Status Code Distribution ---",
        ]
        for status, count in status_counter.most_common():
            lines.append(f"  {status}: {count}")

        lines += ["", "--- Top 20 Paths ---"]
        for path, count in path_counter.most_common(20):
            lines.append(f"  {count:>8}  {path}")

        lines += ["", "--- Top 10 IPs ---"]
        for ip, count in ip_counter.most_common(10):
            lines.append(f"  {count:>8}  {ip}")

        lines += ["", "--- Top Error Paths ---"]
        for ep, count in error_paths.most_common(20):
            lines.append(f"  {count:>8}  {ep}")

        lines += ["", "--- Hourly Distribution ---"]
        for hour in sorted(hourly):
            lines.append(f"  {hour}:00  {hourly[hour]}")

        summary_file.write_text("\n".join(lines) + "\n")
        log.info("Summary written: %s", summary_file)

    return True


def main() -> None:
    """CLI entry point matching original ``parse_access_logs.sh`` interface."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <output_csv> <log_file> [log_file ...]")
        sys.exit(1)

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    output_file = Path(sys.argv[1])
    input_files = [Path(f) for f in sys.argv[2:]]
    summary = output_file.with_suffix(".summary.txt")

    ok = parse_access_logs(input_files, output_file, summary)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
