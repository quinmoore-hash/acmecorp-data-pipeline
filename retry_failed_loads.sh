#!/bin/bash
# ============================================
# Retry Failed Loads
# Re-processes files that ended up in the error directory
# Usually run manually after investigating failures
# Author: mchen
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/file_utils.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/configs/pipeline.env"

MAX_RETRIES="${1:-1}"
DRY_RUN="${2:-false}"

ERROR_FILES=$(find "$DATA_ERROR_DIR" -type f \( -name "*.csv" -o -name "*.json" \) 2>/dev/null)

if [[ -z "$ERROR_FILES" ]]; then
    echo "No failed files found in $DATA_ERROR_DIR"
    exit 0
fi

FILE_COUNT=$(echo "$ERROR_FILES" | wc -l)
echo "Found ${FILE_COUNT} failed files to retry"
echo ""

RETRY_SUCCESS=0
RETRY_FAILED=0

echo "$ERROR_FILES" | while read filepath; do
    BASENAME=$(basename "$filepath")
    EXTENSION="${filepath##*.}"
    
    echo "Processing: $BASENAME"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY RUN] Would retry: $BASENAME"
        continue
    fi
    
    # Apply vendor fixes first
    "${SCRIPT_DIR}/vendor_data_fix.sh" "$filepath"
    
    # Try to transform
    OUTFILE="${DATA_STAGING_DIR}/${BASENAME%.*}_transformed.csv"
    
    if [[ "$EXTENSION" == "csv" ]]; then
        "${SCRIPT_DIR}/transform_csv.sh" "$filepath" "$OUTFILE"
    elif [[ "$EXTENSION" == "json" ]]; then
        "${SCRIPT_DIR}/json_to_csv.sh" "$filepath" "$OUTFILE"
    fi
    
    if [[ $? -eq 0 ]] && [[ -f "$OUTFILE" ]]; then
        "${SCRIPT_DIR}/load_warehouse.sh" "$OUTFILE"
        if [[ $? -eq 0 ]]; then
            echo "  SUCCESS: $BASENAME"
            archive_file "$filepath"
            rm -f "$filepath" "$OUTFILE"
            RETRY_SUCCESS=$((RETRY_SUCCESS + 1))
        else
            echo "  FAILED (load): $BASENAME"
            rm -f "$OUTFILE"
            RETRY_FAILED=$((RETRY_FAILED + 1))
        fi
    else
        echo "  FAILED (transform): $BASENAME"
        RETRY_FAILED=$((RETRY_FAILED + 1))
    fi
done

echo ""
echo "Retry complete: ${RETRY_SUCCESS} succeeded, ${RETRY_FAILED} failed"

if [[ $RETRY_FAILED -gt 0 ]]; then
    send_slack "Retry failed loads: ${RETRY_SUCCESS} succeeded, ${RETRY_FAILED} still failing" "WARNING"
fi
