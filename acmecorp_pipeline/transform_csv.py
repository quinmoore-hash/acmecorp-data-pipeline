"""CSV transformation script — cleans and standardises CSV data for warehouse loading.

Replaces ``transform_csv.sh``.  Uses Python's :mod:`csv` module and
:mod:`re` for transformations instead of ``awk``/``sed`` pipelines.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import load_config
from acmecorp_pipeline.file_utils import check_file, count_data_rows
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("transform_csv")

# Pattern to match MM/DD/YYYY dates
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

# Pattern to match US phone numbers in various formats
_PHONE_RE = re.compile(
    r"^\(?(\d{3})\)?[\-\.\s]?(\d{3})[\-\.\s]?(\d{4})$"
)

# NULL-like sentinel values (case-insensitive)
_NULL_VALUES = {"null", "n/a", "none", "nil"}


def _normalise_date(value: str) -> str:
    """Convert ``MM/DD/YYYY`` to ``YYYY-MM-DD``, returning *value* unchanged
    if it doesn't match."""
    m = _US_DATE_RE.match(value.strip())
    if m:
        month, day, year = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return value


def _normalise_phone(value: str) -> str:
    """Strip non-digits from US phone numbers and add ``+1`` prefix."""
    m = _PHONE_RE.match(value.strip())
    if m:
        digits = "".join(m.groups())
        return f"+1{digits}"
    return value


def _clean_null(value: str) -> str:
    """Replace NULL-like sentinels with empty string."""
    if value.strip().lower() in _NULL_VALUES:
        return ""
    return value


def transform_csv(
    input_file: Path,
    output_file: Path,
    delimiter: str = ",",
    source_label: Optional[str] = None,
) -> bool:
    """Apply cleaning transformations to a CSV and write the result.

    Transformations (matching the original ``transform_csv.sh``):
    1. Remove BOM
    2. Normalise line endings (handled by Python's universal newline mode)
    3. Skip empty rows
    4. Trim whitespace from fields
    5. Standardise dates (``MM/DD/YYYY`` -> ``YYYY-MM-DD``)
    6. Normalise US phone numbers
    7. Replace NULL-like values
    8. Append ``_load_date`` and ``_source_file`` metadata columns

    Returns ``True`` on success.
    """
    if not check_file(input_file):
        return False

    log.info("Transforming: %s", input_file.name)
    today = date.today().isoformat()
    source = source_label or input_file.name

    try:
        with open(input_file, newline="", encoding="utf-8-sig") as fin:
            reader = csv.reader(fin, delimiter=delimiter)
            rows: list[list[str]] = []

            for row_idx, row in enumerate(reader):
                # Skip empty rows
                if not any(cell.strip() for cell in row):
                    continue

                if row_idx == 0:
                    # Header: trim + add metadata columns
                    header = [c.strip() for c in row] + ["_load_date", "_source_file"]
                    rows.append(header)
                    continue

                cleaned: list[str] = []
                for cell in row:
                    cell = cell.strip()
                    cell = _normalise_date(cell)
                    cell = _normalise_phone(cell)
                    cell = _clean_null(cell)
                    cleaned.append(cell)

                cleaned.extend([today, source])
                rows.append(cleaned)

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", newline="") as fout:
            writer = csv.writer(fout, delimiter=delimiter)
            writer.writerows(rows)

        row_count = count_data_rows(output_file)
        log.info("Transform complete: %s (%d rows)", output_file.name, row_count)
        return True

    except Exception as exc:
        log.error("Transform failed for %s: %s", input_file.name, exc)
        return False


def main() -> None:
    """CLI entry point matching original ``transform_csv.sh`` interface."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_csv> <output_csv>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2])

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)

    ok = transform_csv(input_file, output_file, delimiter=cfg.processing.csv_delimiter)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
