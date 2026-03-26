#!/bin/bash
# ============================================
# Database Backup Script
# Daily incremental + weekly full backups
# Author: asingh
# Created: 2021-01-20
#
# Usage:
#   ./db_backup.sh          - incremental backup
#   ./db_backup.sh --full   - full backup
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/utils/lock_manager.sh"
source "${BASE_DIR}/configs/pipeline.env"

BACKUP_DIR="/opt/acmecorp/backups/database"
BACKUP_TYPE="incremental"
RETENTION_DAYS=30
FULL_RETENTION_DAYS=90

# Parse args
if [[ "$1" == "--full" ]]; then
    BACKUP_TYPE="full"
fi

JOB_NAME="db_backup"
trap 'cleanup_lock "$JOB_NAME"' EXIT

acquire_lock "$JOB_NAME" 60 || {
    log_error "Cannot acquire backup lock"
    exit 1
}

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_SUBDIR="${BACKUP_DIR}/${BACKUP_TYPE}/${TIMESTAMP}"
mkdir -p "$BACKUP_SUBDIR"

log_info "Starting ${BACKUP_TYPE} database backup..."
START_TIME=$(date +%s)

if [[ "$BACKUP_TYPE" == "full" ]]; then
    # Full pg_dump
    DUMP_FILE="${BACKUP_SUBDIR}/warehouse_full_${TIMESTAMP}.sql.gz"
    
    PGPASSWORD="$DB_PASSWORD" pg_dump \
        -h "$DB_HOST" \
        -p "$DB_PORT" \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        --verbose \
        --format=custom \
        --compress=9 \
        2>"${BACKUP_SUBDIR}/backup.log" \
        | gzip > "$DUMP_FILE"
    
    BACKUP_RC=$?
else
    # Incremental: only dump tables modified today
    TABLES=$(run_query "production" \
        "SELECT schemaname || '.' || tablename 
         FROM pg_stat_user_tables 
         WHERE last_analyze >= CURRENT_DATE 
            OR last_autoanalyze >= CURRENT_DATE;" 2>/dev/null)
    
    if [[ -z "$TABLES" ]]; then
        log_info "No tables modified today, skipping incremental backup"
        rm -rf "$BACKUP_SUBDIR"
        exit 0
    fi
    
    BACKUP_RC=0
    for table in $TABLES; do
        DUMP_FILE="${BACKUP_SUBDIR}/${table//\./_}_${TIMESTAMP}.sql.gz"
        log_info "Backing up table: $table"
        
        PGPASSWORD="$DB_PASSWORD" pg_dump \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -t "$table" \
            --format=custom \
            --compress=9 \
            2>>"${BACKUP_SUBDIR}/backup.log" \
            | gzip > "$DUMP_FILE"
        
        if [[ $? -ne 0 ]]; then
            log_error "Failed to backup table: $table"
            BACKUP_RC=1
        fi
    done
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
BACKUP_SIZE=$(du -sh "$BACKUP_SUBDIR" 2>/dev/null | awk '{print $1}')

if [[ $BACKUP_RC -eq 0 ]]; then
    log_info "Backup completed: ${BACKUP_TYPE} (${BACKUP_SIZE}, ${DURATION}s)"
    
    # Upload to S3
    aws s3 sync "$BACKUP_SUBDIR" \
        "s3://${S3_BUCKET}/backups/database/${BACKUP_TYPE}/${TIMESTAMP}/" \
        --profile "$AWS_PROFILE" \
        --quiet 2>/dev/null
    
    if [[ $? -ne 0 ]]; then
        log_warn "Failed to upload backup to S3"
        alert "DB backup S3 upload failed (local backup OK)" "WARNING"
    fi
    
    # Write manifest
    echo "type=${BACKUP_TYPE}" > "${BACKUP_SUBDIR}/manifest.txt"
    echo "timestamp=${TIMESTAMP}" >> "${BACKUP_SUBDIR}/manifest.txt"
    echo "size=${BACKUP_SIZE}" >> "${BACKUP_SUBDIR}/manifest.txt"
    echo "duration=${DURATION}" >> "${BACKUP_SUBDIR}/manifest.txt"
    echo "host=${DB_HOST}" >> "${BACKUP_SUBDIR}/manifest.txt"
    echo "database=${DB_NAME}" >> "${BACKUP_SUBDIR}/manifest.txt"
else
    log_error "Backup FAILED: ${BACKUP_TYPE}"
    alert "Database ${BACKUP_TYPE} backup FAILED! Duration: ${DURATION}s" "CRITICAL"
    exit 1
fi

# Cleanup old backups
log_info "Cleaning up old backups..."
find "${BACKUP_DIR}/incremental" -type d -mtime +${RETENTION_DAYS} -exec rm -rf {} + 2>/dev/null
find "${BACKUP_DIR}/full" -type d -mtime +${FULL_RETENTION_DAYS} -exec rm -rf {} + 2>/dev/null

log_info "Backup job complete"
exit 0
