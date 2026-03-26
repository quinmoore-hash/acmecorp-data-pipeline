"""Centralized configuration loading for the AcmeCorp data pipeline.

Loads settings from:
- pipeline.env (shell-style key=value)
- database.conf (INI-style with profiles)
- alerting.conf (INI-style with sections)
- Environment variable overrides
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _find_base_dir() -> Path:
    """Locate the repository / install root.

    Checks, in order:
    1. ``PIPELINE_BASE_DIR`` env var
    2. ``/opt/acmecorp/pipeline`` (production install)
    3. The repo root relative to *this* file (development)
    """
    env = os.environ.get("PIPELINE_BASE_DIR")
    if env:
        return Path(env)
    prod = Path("/opt/acmecorp/pipeline")
    if prod.is_dir():
        return prod
    # Development: this file lives at <repo>/acmecorp_pipeline/config.py
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _find_base_dir()


# ---------------------------------------------------------------------------
# pipeline.env loader
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a shell-style ``KEY=VALUE`` file, ignoring comments and blanks."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            values[key] = value
    return values


# ---------------------------------------------------------------------------
# INI-style config loader
# ---------------------------------------------------------------------------

def _load_ini(path: Path) -> configparser.ConfigParser:
    """Load an INI file, tolerating missing files gracefully."""
    cp = configparser.ConfigParser(interpolation=None)
    if path.is_file():
        cp.read(str(path))
    return cp


# ---------------------------------------------------------------------------
# Typed configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class APIConfig:
    base_url: str = "https://api.internal.acmecorp.com/v2"
    api_key: str = ""
    timeout: int = 30
    retry_count: int = 3


@dataclass
class DatabaseProfile:
    host: str = ""
    port: int = 5432
    dbname: str = ""
    user: str = ""
    password: str = ""
    sslmode: str = "prefer"
    connection_timeout: int = 30
    max_retries: int = 3


@dataclass
class PathsConfig:
    base_dir: Path = Path("/opt/acmecorp")
    input_dir: Path = Path("/opt/acmecorp/data/incoming")
    output_dir: Path = Path("/opt/acmecorp/data/processed")
    archive_dir: Path = Path("/opt/acmecorp/data/archive")
    staging_dir: Path = Path("/opt/acmecorp/data/staging")
    error_dir: Path = Path("/opt/acmecorp/data/errors")
    log_dir: Path = Path("/var/log/acmecorp/pipeline")
    backup_dir: Path = Path("/opt/acmecorp/backups/database")
    ftp_incoming_dir: Path = Path("/opt/acmecorp/data/ftp_incoming")

    @property
    def processed_dir(self) -> Path:
        """Alias for ``output_dir`` used by several pipeline modules."""
        return self.output_dir


@dataclass
class S3Config:
    bucket: str = "acmecorp-data-lake-prod"
    region: str = "us-east-1"
    prefix: str = "raw/daily"
    profile: str = "prod-etl"


@dataclass
class SlackConfig:
    enabled: bool = True
    channel: str = "#data-pipeline-alerts"
    webhook_url: str = ""
    mention_on_critical: str = "@here"


@dataclass
class EmailConfig:
    enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    from_addr: str = ""
    to_addr: str = ""
    cc_addr: str = ""


@dataclass
class PagerDutyConfig:
    enabled: bool = True
    integration_key: str = ""


@dataclass
class AlertThresholds:
    disk_usage_warning: int = 80
    disk_usage_critical: int = 95
    job_runtime_warning_minutes: int = 60
    job_runtime_critical_minutes: int = 120
    error_rate_warning_pct: int = 5
    error_rate_critical_pct: int = 15
    row_count_deviation_pct: int = 20


@dataclass
class ProcessingConfig:
    max_parallel_jobs: int = 4
    batch_size: int = 10000
    csv_delimiter: str = ","
    timestamp_format: str = "%Y-%m-%d %H:%M:%S"
    archive_retention_days: int = 90


@dataclass
class LegacyFTPConfig:
    host: str = "ftp.oldvendor.com"
    user: str = "acme_upload"
    password: str = ""
    remote_dir: str = "/outbound/daily"
    data_format: str = "fixed_width"


@dataclass
class LoggingConfig:
    log_dir: Path = Path("/var/log/acmecorp/pipeline")
    log_level: str = "INFO"
    retention_days: int = 90


@dataclass
class PipelineConfig:
    """Top-level configuration aggregating all sub-configs."""

    api: APIConfig = field(default_factory=APIConfig)
    db_profiles: dict[str, DatabaseProfile] = field(default_factory=dict)
    paths: PathsConfig = field(default_factory=PathsConfig)
    s3: S3Config = field(default_factory=S3Config)
    slack: SlackConfig = field(default_factory=SlackConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    pagerduty: PagerDutyConfig = field(default_factory=PagerDutyConfig)
    thresholds: AlertThresholds = field(default_factory=AlertThresholds)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    legacy_ftp: LegacyFTPConfig = field(default_factory=LegacyFTPConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def load_config(
    base_dir: Optional[Path] = None,
) -> PipelineConfig:
    """Load and merge configuration from all sources.

    Environment variables always override file-based values.
    """
    base = base_dir or BASE_DIR
    env_path = base / "pipeline.env"
    db_conf_path = base / "database.conf"
    alert_conf_path = base / "alerting.conf"

    env = _load_env_file(env_path)
    db_ini = _load_ini(db_conf_path)
    alert_ini = _load_ini(alert_conf_path)

    def _env(key: str, default: str = "") -> str:
        return os.environ.get(key, env.get(key, default))

    # --- API ---
    api = APIConfig(
        base_url=_env("API_BASE_URL", "https://api.internal.acmecorp.com/v2"),
        api_key=_env("API_KEY"),
        timeout=int(_env("API_TIMEOUT", "30")),
        retry_count=int(_env("API_RETRY_COUNT", "3")),
    )

    # --- Database profiles from database.conf ---
    db_profiles: dict[str, DatabaseProfile] = {}
    for section in db_ini.sections():
        db_profiles[section] = DatabaseProfile(
            host=db_ini.get(section, "host", fallback=""),
            port=int(db_ini.get(section, "port", fallback="5432")),
            dbname=db_ini.get(section, "dbname", fallback=db_ini.get(section, "sid", fallback="")),
            user=db_ini.get(section, "user", fallback=""),
            password=db_ini.get(section, "password", fallback=""),
            sslmode=db_ini.get(section, "sslmode", fallback="prefer"),
            connection_timeout=int(db_ini.get(section, "connection_timeout", fallback="30")),
            max_retries=int(db_ini.get(section, "max_retries", fallback="3")),
        )
    # Allow env-var override for the production profile
    if "production" not in db_profiles:
        db_profiles["production"] = DatabaseProfile()
    prod_db = db_profiles["production"]
    prod_db.host = _env("DB_HOST", prod_db.host)
    prod_db.port = int(_env("DB_PORT", str(prod_db.port)))
    prod_db.dbname = _env("DB_NAME", prod_db.dbname)
    prod_db.user = _env("DB_USER", prod_db.user)
    prod_db.password = _env("DB_PASSWORD", prod_db.password)

    # --- Paths ---
    paths = PathsConfig(
        input_dir=Path(_env("DATA_INPUT_DIR", "/opt/acmecorp/data/incoming")),
        output_dir=Path(_env("DATA_OUTPUT_DIR", "/opt/acmecorp/data/processed")),
        archive_dir=Path(_env("DATA_ARCHIVE_DIR", "/opt/acmecorp/data/archive")),
        staging_dir=Path(_env("DATA_STAGING_DIR", "/opt/acmecorp/data/staging")),
        error_dir=Path(_env("DATA_ERROR_DIR", "/opt/acmecorp/data/errors")),
        log_dir=Path(_env("LOG_DIR", "/var/log/acmecorp/pipeline")),
    )

    # --- S3 ---
    s3 = S3Config(
        bucket=_env("S3_BUCKET", "acmecorp-data-lake-prod"),
        region=_env("S3_REGION", "us-east-1"),
        prefix=_env("S3_PREFIX", "raw/daily"),
        profile=_env("AWS_PROFILE", "prod-etl"),
    )

    # --- Alerting ---
    def _alert_int(section: str, key: str, default: int) -> int:
        return int(alert_ini.get(section, key, fallback=str(default)))

    slack = SlackConfig(
        enabled=alert_ini.get("slack", "enabled", fallback="true").lower() == "true",
        channel=alert_ini.get("slack", "channel", fallback="#data-pipeline-alerts"),
        webhook_url=_env("SLACK_WEBHOOK_URL", alert_ini.get("slack", "webhook", fallback="")),
        mention_on_critical=alert_ini.get("slack", "mention_on_critical", fallback="@here"),
    )

    email = EmailConfig(
        enabled=alert_ini.get("email", "enabled", fallback="true").lower() == "true",
        smtp_host=alert_ini.get("email", "smtp_host", fallback=""),
        smtp_port=int(alert_ini.get("email", "smtp_port", fallback="587")),
        from_addr=alert_ini.get("email", "from", fallback=""),
        to_addr=_env("ALERT_EMAIL", alert_ini.get("email", "to", fallback="")),
        cc_addr=alert_ini.get("email", "cc", fallback=""),
    )

    pagerduty = PagerDutyConfig(
        enabled=alert_ini.get("pagerduty", "enabled", fallback="true").lower() == "true",
        integration_key=_env("PAGERDUTY_KEY", alert_ini.get("pagerduty", "integration_key", fallback="")),
    )

    thresholds = AlertThresholds(
        disk_usage_warning=_alert_int("thresholds", "disk_usage_warning", 80),
        disk_usage_critical=_alert_int("thresholds", "disk_usage_critical", 95),
        job_runtime_warning_minutes=_alert_int("thresholds", "job_runtime_warning_minutes", 60),
        job_runtime_critical_minutes=_alert_int("thresholds", "job_runtime_critical_minutes", 120),
        error_rate_warning_pct=_alert_int("thresholds", "error_rate_warning_pct", 5),
        error_rate_critical_pct=_alert_int("thresholds", "error_rate_critical_pct", 15),
        row_count_deviation_pct=_alert_int("thresholds", "row_count_deviation_pct", 20),
    )

    # --- Processing ---
    processing = ProcessingConfig(
        max_parallel_jobs=int(_env("MAX_PARALLEL_JOBS", "4")),
        batch_size=int(_env("BATCH_SIZE", "10000")),
        csv_delimiter=_env("CSV_DELIMITER", ","),
        timestamp_format=_env("TIMESTAMP_FORMAT", "%Y-%m-%d %H:%M:%S"),
    )

    # --- Legacy FTP ---
    legacy_ftp = LegacyFTPConfig(
        host=_env("LEGACY_FTP_HOST", "ftp.oldvendor.com"),
        user=_env("LEGACY_FTP_USER", "acme_upload"),
        password=_env("LEGACY_FTP_PASS", ""),
        data_format=_env("LEGACY_DATA_FORMAT", "fixed_width"),
    )

    # --- Logging ---
    logging_cfg = LoggingConfig(
        log_dir=paths.log_dir,
        log_level=_env("LOG_LEVEL", "INFO"),
        retention_days=int(_env("LOG_RETENTION_DAYS", "90")),
    )

    return PipelineConfig(
        api=api,
        db_profiles=db_profiles,
        paths=paths,
        s3=s3,
        slack=slack,
        email=email,
        pagerduty=pagerduty,
        thresholds=thresholds,
        processing=processing,
        legacy_ftp=legacy_ftp,
        logging=logging_cfg,
    )
