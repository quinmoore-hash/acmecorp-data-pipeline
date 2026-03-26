#!/bin/bash
# ============================================
# Database Restore Script
# Restores from backup created by db_backup.sh
# Author: asingh
#
# Usage:
#   ./db_restore.sh <backup_path>
#   ./db_restore.sh --latest          - restore most recent full backup
#   ./db_restore.sh --list            - list available backups
#
# WARNING: This script drops and recreates tables!
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/configs/pipeline.env"

BACKUP_DIR="/opt/acmecorp/backups/database"

ACTION="$1"

list_backups() {
    echo "Available backups:"
    echo "===================="
    echo ""
    echo "Full backups:"
    ls -lt "${BACKUP_DIR}/full/" 2>/dev/null | head -20
    echo ""
    echo "Incremental backups:"
    ls -lt "${BACKUP_DIR}/incremental/" 2>/dev/null | head -20
}

restore_backup() {
    local backup_path="$1"
    
    if [[ ! -d "$backup_path" ]]; then
        log_error "Backup path not found: $backup_path"
        exit 1
    fi
    
    # Read manifest
    if [[ -f "${backup_path}/manifest.txt" ]]; then
        log_info "Backup manifest:"
        cat "${backup_path}/manifest.txt"
    fi
    
    # Confirmation prompt (only if interactive)
    if [[ -t 0 ]]; then
        echo ""
        echo "WARNING: This will overwrite data in ${DB_NAME} on ${DB_HOST}"
        read -p "Are you sure you want to proceed? (yes/no): " confirm
        if [[ "$confirm" != "yes" ]]; then
            echo "Restore cancelled."
            exit 0
        fi
    fi
    
    log_info "Starting restore from: $backup_path"
    alert "Database restore started from ${backup_path}" "WARNING"
    
    local restore_errors=0
    
    for dump_file in "${backup_path}"/*.sql.gz; do
        [[ -f "$dump_file" ]] || continue
        log_info "Restoring: $(basename "$dump_file")"
        
        gunzip -c "$dump_file" | PGPASSWORD="$DB_PASSWORD" pg_restore \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            --clean \
            --if-exists \
            --no-owner \
            2>&1
        
        if [[ $? -ne 0 ]]; then
            log_error "Restore failed for: $(basename "$dump_file")"
            restore_errors=$((restore_errors + 1))
        fi
    done
    
    if [[ $restore_errors -gt 0 ]]; then
        alert "Database restore completed with ${restore_errors} errors" "CRITICAL"
        exit 1
    else
        log_info "Database restore completed successfully"
        alert "Database restore completed successfully from ${backup_path}" "INFO"
    fi
}

case "$ACTION" in
    --list)
        list_backups
        ;;
    --latest)
        LATEST=$(ls -td "${BACKUP_DIR}/full/"*/ 2>/dev/null | head -1)
        if [[ -z "$LATEST" ]]; then
            echo "No full backups found"
            exit 1
        fi
        echo "Latest full backup: $LATEST"
        restore_backup "$LATEST"
        ;;
    "")
        echo "Usage: $0 <backup_path|--latest|--list>"
        exit 1
        ;;
    *)
        restore_backup "$ACTION"
        ;;
esac
