#!/bin/bash
# ============================================
# Reprocess Date Range
# Re-runs the ETL pipeline for a specific date range
# Used for backfills and data corrections
# Author: mchen
# Created: 2023-04-05
#
# Usage: ./reprocess_date_range.sh 2024-01-01 2024-01-15
# WARNING: This deletes existing data for the date range first!
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/configs/pipeline.env"

START_DATE="$1"
END_DATE="$2"
TABLES_FILTER="$3"  # optional: comma-separated list of tables

if [[ -z "$START_DATE" ]] || [[ -z "$END_DATE" ]]; then
    echo "Usage: $0 <start_date> <end_date> [tables]"
    echo "  Dates in YYYY-MM-DD format"
    echo "  Tables: comma-separated, e.g. 'vendor_a_orders,vendor_b_transactions'"
    exit 1
fi

# Validate dates (basic check)
if ! date -d "$START_DATE" &>/dev/null 2>&1 && ! date -j -f "%Y-%m-%d" "$START_DATE" &>/dev/null 2>&1; then
    echo "Invalid start date: $START_DATE"
    exit 1
fi

ALL_TABLES=(
    "raw_ingest.vendor_a_orders"
    "raw_ingest.vendor_a_inventory"
    "raw_ingest.vendor_b_transactions"
    "raw_ingest.vendor_c_shipments"
    "raw_ingest.customer_data"
    "raw_ingest.product_catalog"
)

# Filter tables if specified
if [[ -n "$TABLES_FILTER" ]]; then
    TABLES=()
    IFS=',' read -ra FILTER_LIST <<< "$TABLES_FILTER"
    for t in "${FILTER_LIST[@]}"; do
        for full_t in "${ALL_TABLES[@]}"; do
            if [[ "$full_t" == *"$t"* ]]; then
                TABLES+=("$full_t")
            fi
        done
    done
else
    TABLES=("${ALL_TABLES[@]}")
fi

log_info "Reprocessing date range: ${START_DATE} to ${END_DATE}"
log_info "Tables: ${TABLES[*]}"
alert "Reprocess started: ${START_DATE} to ${END_DATE} (${#TABLES[@]} tables)" "WARNING"

# Confirmation
if [[ -t 0 ]]; then
    echo ""
    echo "This will DELETE and RELOAD data for:"
    echo "  Date range: ${START_DATE} to ${END_DATE}"
    echo "  Tables: ${TABLES[*]}"
    echo ""
    read -p "Continue? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

# Step 1: Delete existing data for the date range
log_info "Deleting existing data for date range..."
for table in "${TABLES[@]}"; do
    DELETED=$(run_query "production" \
        "DELETE FROM ${table} WHERE _load_date >= '${START_DATE}' AND _load_date <= '${END_DATE}' RETURNING 1;" 2>/dev/null | wc -l)
    log_info "Deleted ${DELETED} rows from ${table}"
done

# Step 2: Download archived files from S3
log_info "Downloading archived files from S3..."
REPROCESS_DIR="${DATA_STAGING_DIR}/reprocess_$$"
mkdir -p "$REPROCESS_DIR"

CURRENT_DATE="$START_DATE"
while [[ "$CURRENT_DATE" < "$END_DATE" ]] || [[ "$CURRENT_DATE" == "$END_DATE" ]]; do
    YEAR=$(echo "$CURRENT_DATE" | cut -d- -f1)
    MONTH=$(echo "$CURRENT_DATE" | cut -d- -f2)
    DAY=$(echo "$CURRENT_DATE" | cut -d- -f3)
    
    aws s3 sync \
        "s3://${S3_BUCKET}/archive/${YEAR}/${MONTH}/${DAY}/" \
        "${REPROCESS_DIR}/" \
        --profile "$AWS_PROFILE" \
        --quiet 2>/dev/null
    
    # Increment date (GNU vs BSD date)
    CURRENT_DATE=$(date -d "${CURRENT_DATE} + 1 day" +%Y-%m-%d 2>/dev/null || \
                   date -j -v+1d -f "%Y-%m-%d" "$CURRENT_DATE" +%Y-%m-%d 2>/dev/null)
done

FILE_COUNT=$(find "$REPROCESS_DIR" -type f | wc -l)
log_info "Downloaded ${FILE_COUNT} archived files"

# Step 3: Reprocess each file
log_info "Reprocessing files..."
PROCESSED=0
FAILED=0

for datafile in "${REPROCESS_DIR}"/*.{csv,json}; do
    [[ -f "$datafile" ]] || continue
    
    EXTENSION="${datafile##*.}"
    OUTFILE="${REPROCESS_DIR}/$(basename "${datafile%.*}")_transformed.csv"
    
    # Apply fixes first
    "${SCRIPT_DIR}/vendor_data_fix.sh" "$datafile" 2>/dev/null
    
    if [[ "$EXTENSION" == "csv" ]]; then
        "${SCRIPT_DIR}/transform_csv.sh" "$datafile" "$OUTFILE" 2>/dev/null
    elif [[ "$EXTENSION" == "json" ]]; then
        "${SCRIPT_DIR}/json_to_csv.sh" "$datafile" "$OUTFILE" 2>/dev/null
    fi
    
    if [[ $? -eq 0 ]] && [[ -f "$OUTFILE" ]]; then
        "${SCRIPT_DIR}/load_warehouse.sh" "$OUTFILE" 2>/dev/null
        if [[ $? -eq 0 ]]; then
            PROCESSED=$((PROCESSED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    else
        FAILED=$((FAILED + 1))
    fi
done

# Cleanup
rm -rf "$REPROCESS_DIR"

log_info "Reprocess complete: ${PROCESSED} succeeded, ${FAILED} failed"
alert "Reprocess finished: ${START_DATE} to ${END_DATE}. ${PROCESSED} OK, ${FAILED} failed." \
    "$([ $FAILED -gt 0 ] && echo 'WARNING' || echo 'INFO')"

exit $([ $FAILED -gt 0 ] && echo 1 || echo 0)
