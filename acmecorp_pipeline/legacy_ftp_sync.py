"""Legacy FTP sync — downloads fixed-width data files from vendor FTP servers.

Replaces ``legacy_ftp_sync.sh``.  Uses Python's :mod:`ftplib` instead
of shell ``ftp`` / ``lftp`` and parses fixed-width files with
:func:`struct.unpack`.
"""

from __future__ import annotations

import csv
import ftplib
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from acmecorp_pipeline.config import PipelineConfig, load_config
from acmecorp_pipeline.file_utils import archive_file
from acmecorp_pipeline.logging_utils import get_logger, setup_logging
from acmecorp_pipeline.notifications import alert

log = get_logger("legacy_ftp_sync")

# Fixed-width field definitions: (name, start, length)
FIXED_WIDTH_FIELDS = [
    ("record_type", 0, 2),
    ("account_id", 2, 10),
    ("transaction_date", 12, 8),  # YYYYMMDD
    ("amount", 20, 12),
    ("currency", 32, 3),
    ("description", 35, 40),
    ("reference", 75, 20),
    ("status", 95, 1),
]

RECORD_LENGTH = 96  # Total expected line length


def _connect_ftp(config: PipelineConfig) -> ftplib.FTP:
    """Open an FTP connection using legacy FTP config."""
    ftp_cfg = config.legacy_ftp
    log.info("Connecting to FTP: %s", ftp_cfg.host)

    ftp = ftplib.FTP()
    ftp.connect(ftp_cfg.host, ftp_cfg.port, timeout=30)
    ftp.login(ftp_cfg.user, ftp_cfg.password)

    if ftp_cfg.remote_dir:
        ftp.cwd(ftp_cfg.remote_dir)

    return ftp


def _download_files(ftp: ftplib.FTP, local_dir: Path, pattern: str = "*.dat") -> list[Path]:
    """Download all matching files from the FTP server."""
    local_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    try:
        filenames = ftp.nlst()
    except ftplib.error_perm:
        log.warning("FTP directory listing failed")
        return downloaded

    for fname in filenames:
        if not fname.endswith(".dat") and not fname.endswith(".txt"):
            continue

        local_path = local_dir / fname
        log.info("Downloading: %s", fname)

        try:
            with open(local_path, "wb") as fh:
                ftp.retrbinary(f"RETR {fname}", fh.write)
            downloaded.append(local_path)
        except ftplib.error_perm as exc:
            log.error("FTP download failed for %s: %s", fname, exc)

    return downloaded


def parse_fixed_width(input_file: Path, output_file: Path) -> int:
    """Parse a fixed-width file and write a CSV.

    Returns the number of data rows written, or ``-1`` on failure.
    """
    log.info("Parsing fixed-width: %s", input_file.name)

    rows: list[dict[str, str]] = []
    errors = 0

    with open(input_file, errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.rstrip("\n\r")
            if not line.strip():
                continue

            if len(line) < RECORD_LENGTH:
                log.warning("Short line %d in %s (%d chars)", line_no, input_file.name, len(line))
                errors += 1
                continue

            row: dict[str, str] = {}
            for field_name, start, length in FIXED_WIDTH_FIELDS:
                value = line[start:start + length].strip()

                # Normalise transaction_date YYYYMMDD -> YYYY-MM-DD
                if field_name == "transaction_date" and len(value) == 8:
                    try:
                        dt = datetime.strptime(value, "%Y%m%d")
                        value = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        pass

                # Normalise amount — remove leading zeros, add decimal
                if field_name == "amount":
                    try:
                        cents = int(value)
                        value = f"{cents / 100:.2f}"
                    except ValueError:
                        pass

                row[field_name] = value

            rows.append(row)

    if not rows:
        log.warning("No valid records in %s", input_file.name)
        return 0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [f[0] for f in FIXED_WIDTH_FIELDS]

    with open(output_file, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info("Parsed %d records (%d errors) -> %s", len(rows), errors, output_file.name)
    return len(rows)


def run_ftp_sync(config: PipelineConfig) -> bool:
    """Execute the full FTP sync workflow.

    1. Connect to FTP
    2. Download new files
    3. Parse fixed-width -> CSV
    4. Archive originals

    Returns ``True`` on success.
    """
    log.info("Starting legacy FTP sync...")

    ftp: Optional[ftplib.FTP] = None
    try:
        ftp = _connect_ftp(config)
    except Exception as exc:
        log.error("FTP connection failed: %s", exc)
        alert(f"Legacy FTP sync failed to connect: {exc}", "WARNING", config)
        return False

    try:
        download_dir = config.paths.input_dir / "ftp_incoming"
        files = _download_files(ftp, download_dir)
        log.info("Downloaded %d files", len(files))
    finally:
        try:
            ftp.quit()
        except Exception:
            pass

    if not files:
        log.info("No new FTP files to process")
        return True

    success = True
    for datafile in files:
        csv_output = config.paths.staging_dir / f"{datafile.stem}.csv"
        n = parse_fixed_width(datafile, csv_output)
        if n <= 0:
            success = False
        else:
            archive_file(datafile, config.paths.archive_dir)

    log.info("FTP sync complete: %d files processed", len(files))
    return success


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.log_cfg.log_level)
    ok = run_ftp_sync(cfg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
