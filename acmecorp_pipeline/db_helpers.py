"""Database helper functions wrapping PostgreSQL operations.

Replaces ``db_helpers.sh``.  Uses :mod:`psycopg2` instead of shelling
out to ``psql``, providing proper connection pooling, parameterised
queries, and structured error handling.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from acmecorp_pipeline.config import DatabaseProfile, PipelineConfig
from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("db_helpers")


def _get_profile(config: PipelineConfig, profile: str = "production") -> DatabaseProfile:
    """Resolve a named database profile from config."""
    if profile not in config.db_profiles:
        raise ValueError(f"Unknown database profile: {profile}")
    return config.db_profiles[profile]


def get_connection(
    config: PipelineConfig,
    profile: str = "production",
) -> psycopg2.extensions.connection:
    """Open and return a new database connection for the given profile."""
    db = _get_profile(config, profile)
    return psycopg2.connect(
        host=db.host,
        port=db.port,
        dbname=db.dbname,
        user=db.user,
        password=db.password,
        sslmode=db.sslmode,
        connect_timeout=db.connection_timeout,
    )


def get_conn_string(config: PipelineConfig, profile: str = "production") -> str:
    """Build a ``postgresql://`` connection URI."""
    db = _get_profile(config, profile)
    return f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.dbname}"


def run_query(
    config: PipelineConfig,
    query: str,
    params: Optional[tuple[Any, ...]] = None,
    profile: str = "production",
    output_file: Optional[Path] = None,
) -> Optional[list[tuple[Any, ...]]]:
    """Execute a SQL query and return all result rows (or ``None`` on error).

    If *output_file* is given the raw text output is written there.
    """
    try:
        conn = get_connection(config, profile)
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description is not None:
                    rows = cur.fetchall()
                else:
                    rows = []
                conn.commit()

                if output_file is not None:
                    output_file.write_text(
                        "\n".join("\t".join(str(c) for c in row) for row in rows)
                    )
                return rows
        finally:
            conn.close()
    except psycopg2.Error as exc:
        log.error("Query failed (profile=%s): %s — %s", profile, query[:100], exc)
        return None


def run_sql_file(
    config: PipelineConfig,
    sql_file: Path,
    profile: str = "production",
) -> bool:
    """Execute all statements in *sql_file*.  Returns ``True`` on success."""
    if not sql_file.is_file():
        log.error("SQL file not found: %s", sql_file)
        return False

    sql = sql_file.read_text()
    try:
        conn = get_connection(config, profile)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            return True
        finally:
            conn.close()
    except psycopg2.Error as exc:
        log.error("SQL file execution failed (%s): %s", sql_file, exc)
        return False


def check_db_connection(
    config: PipelineConfig,
    profile: str = "production",
) -> bool:
    """Test database connectivity with retries.  Returns ``True`` on success."""
    db = _get_profile(config, profile)
    max_retries = db.max_retries

    for attempt in range(1, max_retries + 1):
        try:
            conn = get_connection(config, profile)
            conn.close()
            log.info("Database connection OK (profile=%s)", profile)
            return True
        except psycopg2.Error:
            log.warning(
                "DB connection attempt %d/%d failed (profile=%s)",
                attempt, max_retries, profile,
            )
            if attempt < max_retries:
                time.sleep(5)

    log.error("Cannot connect to database (profile=%s)", profile)
    return False


def get_table_count(
    config: PipelineConfig,
    table: str,
    profile: str = "production",
) -> int:
    """Return the row count of *table*, or ``0`` on error."""
    rows = run_query(config, f"SELECT COUNT(*) FROM {table};", profile=profile)
    if rows and rows[0]:
        return int(rows[0][0])
    return 0


def copy_from_csv(
    config: PipelineConfig,
    csv_path: Path,
    table: str,
    profile: str = "production",
) -> bool:
    """Bulk-load a CSV file into *table* using ``COPY ... FROM STDIN``.

    Returns ``True`` on success.
    """
    try:
        conn = get_connection(config, profile)
        try:
            with conn.cursor() as cur, open(csv_path) as fh:
                cur.copy_expert(
                    f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',', NULL '')",
                    fh,
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except (psycopg2.Error, OSError) as exc:
        log.error("COPY failed for %s -> %s: %s", csv_path, table, exc)
        return False
