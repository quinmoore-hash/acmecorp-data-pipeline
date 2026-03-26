"""API data fetcher with pagination and retry support.

Replaces ``fetch_api_data.sh``.  Uses :mod:`requests` instead of ``curl``
and writes well-formed JSON output.
"""

from __future__ import annotations

import json
import time
from base64 import b64encode
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests

from acmecorp_pipeline.config import PipelineConfig
from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("fetch_api_data")

# Vendor-specific API configurations
VENDOR_CONFIGS: dict[str, dict[str, Any]] = {
    "vendor-a": {
        "base_url": "https://api.vendor-a.com/v3",
        "auth_type": "api_key",
        "auth_header": "X-Api-Key",
        "page_size": 500,
    },
    "vendor-b": {
        "base_url": "https://data.vendor-b.io/api",
        "auth_type": "bearer",
        "page_size": 1000,
    },
    "vendor-c": {
        "base_url": "https://portal.vendor-c.net/export",
        "auth_type": "basic",
        "basic_user": "acme",
        "basic_pass": "v3nd0rC_2023",
        "page_size": 200,
    },
}


def _build_headers(
    vendor_cfg: dict[str, Any],
    api_key: str,
) -> dict[str, str]:
    """Build HTTP headers for the vendor's authentication scheme."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    auth_type = vendor_cfg["auth_type"]

    if auth_type == "api_key":
        headers[vendor_cfg["auth_header"]] = api_key
    elif auth_type == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_type == "basic":
        token = b64encode(
            f"{vendor_cfg['basic_user']}:{vendor_cfg['basic_pass']}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"

    return headers


def fetch_api_data(
    vendor: str,
    endpoint: str,
    output_file: Path,
    config: PipelineConfig,
) -> int:
    """Fetch data from a vendor API with pagination.

    Args:
        vendor: Vendor key (``vendor-a``, ``vendor-b``, ``vendor-c``).
        endpoint: API endpoint path (e.g. ``orders``, ``transactions``).
        output_file: Path to write the collected JSON records.
        config: Pipeline configuration.

    Returns:
        Total number of records fetched, or ``-1`` on failure.
    """
    if vendor not in VENDOR_CONFIGS:
        log.error("Unknown vendor: %s", vendor)
        return -1

    vcfg = VENDOR_CONFIGS[vendor]
    base_url = vcfg["base_url"]
    page_size = vcfg["page_size"]
    headers = _build_headers(vcfg, config.api.api_key)

    url = f"{base_url}/{endpoint}"
    all_records: list[dict[str, Any]] = []
    page = 1
    max_pages = 100
    today = date.today().isoformat()

    log.info("Fetching %s/%s -> %s", vendor, endpoint, output_file)

    while page <= max_pages:
        log.debug("Fetching page %d...", page)

        params = {"page": page, "per_page": page_size, "date": today}
        resp: Optional[requests.Response] = None

        for attempt in range(1, config.api.retry_count + 1):
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=(config.api.timeout, 120),
                )
                if resp.status_code == 200:
                    break
                log.warning(
                    "API returned HTTP %d (attempt %d/%d), retrying in 10s...",
                    resp.status_code, attempt, config.api.retry_count,
                )
                time.sleep(10)
            except requests.RequestException as exc:
                log.warning("API request failed (attempt %d/%d): %s", attempt, config.api.retry_count, exc)
                time.sleep(10)

        if resp is None or resp.status_code != 200:
            log.error(
                "API fetch failed after %d retries for %s/%s",
                config.api.retry_count, vendor, endpoint,
            )
            return -1

        # Extract records — vendors use different envelope keys
        data = resp.json()
        records = data.get("data") or data.get("results") or data.get("records") or []

        if not records:
            break

        all_records.extend(records)

        if len(records) < page_size:
            break

        page += 1
        # Rate limiting
        time.sleep(0.5)

    # Write output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as fh:
        json.dump(all_records, fh)

    log.info("Fetched %d records from %s/%s", len(all_records), vendor, endpoint)
    return len(all_records)


def main() -> None:
    """CLI entry point matching original ``fetch_api_data.sh`` interface."""
    import sys

    from acmecorp_pipeline.config import load_config
    from acmecorp_pipeline.logging_utils import setup_logging

    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <vendor> <endpoint> <output_file>")
        sys.exit(1)

    vendor = sys.argv[1]
    endpoint = sys.argv[2]
    output_file = Path(sys.argv[3])

    cfg = load_config()
    setup_logging(cfg.paths.log_dir, cfg.logging.log_level)

    result = fetch_api_data(vendor, endpoint, output_file, cfg)
    sys.exit(0 if result >= 0 else 1)


if __name__ == "__main__":
    main()
