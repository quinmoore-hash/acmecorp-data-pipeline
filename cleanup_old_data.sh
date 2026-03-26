#!/bin/bash
# ============================================
# Data Cleanup Script
# Removes old processed files, logs, and temp data
# Runs daily at 6:00 AM
# Author: jthompson
# 
# BUG: sometimes deletes files that are still being processed
# if nightly ETL runs long. Added sleep as workaround.
# TODO: use lock file instead of sleep
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/configs/pipeline.env"

log_info "Starting data cleanup..."

# Wait for any running ETL jobs to finish (hacky but works)
sleep 30

TOTAL_FREED=0

# Clean processed data older than 7 days
log_info "Cleaning processed data..."
OLD_FILES=$(find "$DATA_OUTPUT_DIR" -type f -mtime +7 2>/dev/null)
if [[ -n "$OLD_FILES" ]]; then
    SIZE=$(echo "$OLD_FILES" | xargs du -sc 2>/dev/null | tail -1 | awk '{print $1}')
    echo "$OLD_FILES" | xargs rm -f 2>/dev/null
    TOTAL_FREED=$((TOTAL_FREED + SIZE))
    log_info "Removed $(echo "$OLD_FILES" | wc -l) processed files (${SIZE}KB)"
fi

# Clean staging directory (should be empty, but just in case)
log_info "Cleaning staging area..."
rm -f "${DATA_STAGING_DIR}"/*.csv 2>/dev/null
rm -f "${DATA_STAGING_DIR}"/*.json 2>/dev/null
rm -f "${DATA_STAGING_DIR}"/*.tmp 2>/dev/null

# Clean error files older than 30 days
log_info "Cleaning error files..."
find "$DATA_ERROR_DIR" -type f -mtime +30 -delete 2>/dev/null

# Clean archive older than 60 days (S3 should have a copy)
log_info "Cleaning local archives..."
OLD_ARCHIVES=$(find "$DATA_ARCHIVE_DIR" -type f -mtime +60 2>/dev/null)
if [[ -n "$OLD_ARCHIVES" ]]; then
    SIZE=$(echo "$OLD_ARCHIVES" | xargs du -sc 2>/dev/null | tail -1 | awk '{print $1}')
    echo "$OLD_ARCHIVES" | xargs rm -f 2>/dev/null
    TOTAL_FREED=$((TOTAL_FREED + SIZE))
    log_info "Removed $(echo "$OLD_ARCHIVES" | wc -l) archive files (${SIZE}KB)"
fi

# Rotate and clean logs
log_info "Rotating logs..."
# Compress logs older than 3 days
find "$LOG_DIR" -name "*.log" -mtime +3 ! -name "*.gz" -exec gzip {} \; 2>/dev/null

# Remove compressed logs older than 90 days
find "$LOG_DIR" -name "*.log.gz" -mtime +${LOG_RETENTION_DAYS} -delete 2>/dev/null

# Clean temp files
log_info "Cleaning temp files..."
rm -rf /tmp/acmecorp_* 2>/dev/null
# ^ WARNING: this also removes lock files! Known issue but nobody has fixed it

# Clean old PID files
find /var/run/acmecorp -name "*.pid" -mtime +1 -delete 2>/dev/null

log_info "Cleanup complete. Freed approximately ${TOTAL_FREED}KB"
