"""JSON to CSV converter for warehouse loading.

Replaces ``json_to_csv.sh``.  Uses Python's :mod:`json` and :mod:`csv`
modules instead of ``jq``, handling nested objects more gracefully.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

from acmecorp_pipeline.file_utils import check_file
from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("json_to_csv")


def _flatten_value(value: Any) -> str:
    """Convert a value to a CSV-safe string, serialising nested structures."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def json_to_csv(input_file: Path, output_file: Path) -> int:
    """Convert a JSON file (array or single object) to CSV.

    Args:
        input_file: Path to JSON input.
        output_file: Path to write CSV output.

    Returns:
        Number of data rows written, or ``-1`` on failure.
    """
    if not check_file(input_file):
        return -1

    log.info("Converting JSON to CSV: %s", input_file.name)

    try:
        with open(input_file) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to read JSON: %s — %s", input_file, exc)
        return -1

    # Normalise to list
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list) or len(data) == 0:
        log.error("Could not extract records from JSON: %s", input_file)
        return -1

    # Extract headers from the union of all keys (preserve first-record order)
    headers: list[str] = []
    seen: set[str] = set()
    for record in data:
        if isinstance(record, dict):
            for key in record:
                if key not in seen:
                    headers.append(key)
                    seen.add(key)

    if not headers:
        log.error("Could not extract headers from JSON: %s", input_file)
        return -1

    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_file, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            for record in data:
                if isinstance(record, dict):
                    row = [_flatten_value(record.get(h)) for h in headers]
                    writer.writerow(row)
    except OSError as exc:
        log.error("Failed to write CSV: %s — %s", output_file, exc)
        return -1

    rows = len(data)
    log.info("Converted %s -> %s (%d rows)", input_file.name, output_file.name, rows)
    return rows


def main() -> None:
    """CLI entry point matching original ``json_to_csv.sh`` interface."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_json> <output_csv>")
        sys.exit(1)

    from acmecorp_pipeline.config import load_config
    from acmecorp_pipeline.logging_utils import setup_logging

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    result = json_to_csv(Path(sys.argv[1]), Path(sys.argv[2]))
    sys.exit(0 if result >= 0 else 1)


if __name__ == "__main__":
    main()
