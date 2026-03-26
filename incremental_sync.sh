#!/bin/bash
# ============================================
# Incremental Data Sync
# Runs hourly to pick up mid-day data drops
# Lighter version of the nightly ETL
# Author: mchen
# Created: 2022-04-11
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/file_utils.sh"
source "${BASE_DIR}/utils/lock_manager.sh"
source "${BASE_DIR}/configs/pipeline.env"

JOB_NAME="incremental_sync"
trap 'cleanup_lock "$JOB_NAME"' EXIT

acquire_lock "$JOB_NAME" 30 || {
    log_info "Incremental sync already running, skipping"
    exit 0
}

log_info "Starting incremental sync..."

# Only process files that arrived since last run
MARKER_FILE="/tmp/acmecorp_last_incremental"
LAST_RUN=$(cat "$MARKER_FILE" 2>/dev/null || echo "0")

NEW_FILES=0

# Check for new files in input directory
for datafile in "${DATA_INPUT_DIR}"/*.{csv,json}; do
    [[ -f "$datafile" ]] || continue
    
    FILE_MTIME=$(stat -f%m "$datafile" 2>/dev/null || stat -c%Y "$datafile" 2>/dev/null)
    
    if [[ "$FILE_MTIME" -gt "$LAST_RUN" ]]; then
        log_info "New file detected: $(basename "$datafile")"
        NEW_FILES=$((NEW_FILES + 1))
        
        EXTENSION="${datafile##*.}"
        BASENAME=$(basename "$datafile")
        OUTFILE="${DATA_STAGING_DIR}/${BASENAME%.*}_transformed.csv"
        
        if [[ "$EXTENSION" == "csv" ]]; then
            "${SCRIPT_DIR}/transform_csv.sh" "$datafile" "$OUTFILE"
        elif [[ "$EXTENSION" == "json" ]]; then
            "${SCRIPT_DIR}/json_to_csv.sh" "$datafile" "$OUTFILE"
        fi
        
        if [[ $? -eq 0 ]] && [[ -f "$OUTFILE" ]]; then
            "${SCRIPT_DIR}/load_warehouse.sh" "$OUTFILE"
            if [[ $? -eq 0 ]]; then
                archive_file "$datafile"
                rm -f "$datafile" "$OUTFILE"
            fi
        fi
    fi
done

# Update marker
date +%s > "$MARKER_FILE"

if [[ $NEW_FILES -eq 0 ]]; then
    log_info "No new files to process"
else
    log_info "Incremental sync complete: processed ${NEW_FILES} files"
fi

exit 0
