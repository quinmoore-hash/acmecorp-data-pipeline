"""Vendor data fix — applies known corrections to vendor data quirks.

Replaces ``vendor_data_fix.sh``.  Handles encoding issues, date format
mismatches, duplicate records, and vendor-specific field mappings.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import load_config
from acmecorp_pipeline.file_utils import check_file
from acmecorp_pipeline.logging_utils import get_logger, setup_logging

log = get_logger("vendor_data_fix")


# ---------------------------------------------------------------------------
# Vendor-specific fixups
# ---------------------------------------------------------------------------

def _fix_vendor_a(rows: list[dict[str, str]], header: list[str]) -> list[dict[str, str]]:
    """Vendor A fixes:
    - ``order_total`` sometimes uses comma as decimal separator
    - ``state`` field may contain full state names instead of codes
    """
    state_map = {
        "california": "CA", "new york": "NY", "texas": "TX",
        "florida": "FL", "illinois": "IL", "pennsylvania": "PA",
        "ohio": "OH", "georgia": "GA", "north carolina": "NC",
        "michigan": "MI", "new jersey": "NJ", "virginia": "VA",
        "washington": "WA", "arizona": "AZ", "massachusetts": "MA",
        "tennessee": "TN", "indiana": "IN", "missouri": "MO",
        "maryland": "MD", "wisconsin": "WI", "colorado": "CO",
        "minnesota": "MN", "south carolina": "SC", "alabama": "AL",
        "louisiana": "LA", "kentucky": "KY", "oregon": "OR",
        "oklahoma": "OK", "connecticut": "CT", "utah": "UT",
        "iowa": "IA", "nevada": "NV", "arkansas": "AR",
        "mississippi": "MS", "kansas": "KS", "nebraska": "NE",
    }

    for row in rows:
        # Fix comma decimals
        for field in ("order_total", "total_amount", "subtotal", "tax"):
            if field in row and "," in row[field]:
                row[field] = row[field].replace(",", ".")

        # Normalise state names
        if "state" in row:
            lower = row["state"].strip().lower()
            if lower in state_map:
                row["state"] = state_map[lower]

    return rows


def _fix_vendor_b(rows: list[dict[str, str]], header: list[str]) -> list[dict[str, str]]:
    """Vendor B fixes:
    - ``amount`` field sometimes has currency symbol prefix
    - ``timestamp`` uses non-standard format
    """
    currency_re = re.compile(r"^[£€$¥]\s*")

    for row in rows:
        if "amount" in row:
            row["amount"] = currency_re.sub("", row["amount"])

        # Normalise DD-MM-YYYY -> YYYY-MM-DD
        for field in ("timestamp", "transaction_date", "created_at"):
            if field in row:
                m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", row[field].strip())
                if m:
                    day, month, year = m.groups()
                    row[field] = f"{year}-{month}-{day}"

    return rows


def _fix_vendor_c(rows: list[dict[str, str]], header: list[str]) -> list[dict[str, str]]:
    """Vendor C fixes:
    - ``weight_kg`` sometimes in pounds (indicated by 'lb' suffix)
    - Column name inconsistencies (``shipTo`` -> ``ship_to``)
    """
    # Rename columns in header
    renames = {
        "shipTo": "ship_to",
        "shipFrom": "ship_from",
        "trackingNum": "tracking_number",
        "shipDate": "ship_date",
    }

    for row in rows:
        # Fix weight units
        if "weight_kg" in row and row["weight_kg"].lower().endswith("lb"):
            try:
                lbs = float(row["weight_kg"].lower().replace("lb", "").strip())
                row["weight_kg"] = f"{lbs * 0.453592:.2f}"
            except ValueError:
                pass

        # Rename keys
        for old_key, new_key in renames.items():
            if old_key in row:
                row[new_key] = row.pop(old_key)

    return rows


def _deduplicate(rows: list[dict[str, str]], key_field: str) -> list[dict[str, str]]:
    """Remove duplicate rows based on a key field, keeping the last occurrence."""
    seen: dict[str, int] = {}
    for idx, row in enumerate(rows):
        key = row.get(key_field, "")
        if key:
            seen[key] = idx

    if len(seen) < len(rows):
        deduped = [rows[idx] for idx in sorted(seen.values())]
        log.info("Deduplicated: %d -> %d rows (key=%s)", len(rows), len(deduped), key_field)
        return deduped

    return rows


VENDOR_FIXUPS = {
    "vendor-a": (_fix_vendor_a, "order_id"),
    "vendor-b": (_fix_vendor_b, "transaction_id"),
    "vendor-c": (_fix_vendor_c, "shipment_id"),
}


def fix_vendor_data(
    input_file: Path,
    output_file: Path,
    vendor: str,
    deduplicate_key: Optional[str] = None,
) -> bool:
    """Apply vendor-specific fixes to a CSV file.

    Args:
        input_file: Source CSV file.
        output_file: Destination CSV file.
        vendor: Vendor key (``vendor-a``, ``vendor-b``, ``vendor-c``).
        deduplicate_key: Column name for deduplication (auto-detected if None).

    Returns:
        ``True`` on success.
    """
    if not check_file(input_file):
        return False

    if vendor not in VENDOR_FIXUPS:
        log.error("Unknown vendor: %s", vendor)
        return False

    fix_fn, default_key = VENDOR_FIXUPS[vendor]
    dedup_key = deduplicate_key or default_key

    log.info("Fixing vendor data: %s (%s)", input_file.name, vendor)

    try:
        with open(input_file, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            rows = list(reader)
    except Exception as exc:
        log.error("Failed to read %s: %s", input_file, exc)
        return False

    original_count = len(rows)

    # Apply vendor-specific fixes
    rows = fix_fn(rows, header)

    # Deduplicate
    rows = _deduplicate(rows, dedup_key)

    # Write output
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Build output header from first row keys (may have been renamed)
    out_header = list(rows[0].keys()) if rows else header

    try:
        with open(output_file, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=out_header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except Exception as exc:
        log.error("Failed to write %s: %s", output_file, exc)
        return False

    log.info(
        "Vendor fix complete: %s — %d -> %d rows",
        output_file.name, original_count, len(rows),
    )
    return True


def main() -> None:
    """CLI entry point matching original ``vendor_data_fix.sh`` interface."""
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <vendor> <input_csv> <output_csv>")
        sys.exit(1)

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    vendor = sys.argv[1]
    input_file = Path(sys.argv[2])
    output_file = Path(sys.argv[3])

    ok = fix_vendor_data(input_file, output_file, vendor)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
