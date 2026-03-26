#!/bin/bash
# ============================================
# Warehouse Data Loader
# Loads transformed CSV files into PostgreSQL warehouse
# Uses COPY for bulk loading
# Author: jthompson
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/utils/file_utils.sh"
source "${BASE_DIR}/configs/pipeline.env"

INPUT_FILE="$1"
TARGET_TABLE="$2"

if [[ -z "$INPUT_FILE" ]]; then
    echo "Usage: $0 <csv_file> [target_table]"
    exit 1
fi

check_file "$INPUT_FILE" || exit 1

# Auto-detect target table from filename if not specified
if [[ -z "$TARGET_TABLE" ]]; then
    BASENAME=$(basename "$INPUT_FILE" | sed 's/_transformed.csv//' | sed 's/_[0-9]*$//')
    # Map known file patterns to tables
    case "$BASENAME" in
        vendor-a_orders*)   TARGET_TABLE="raw_ingest.vendor_a_orders" ;;
        vendor-a_inventory*) TARGET_TABLE="raw_ingest.vendor_a_inventory" ;;
        vendor-b_transactions*) TARGET_TABLE="raw_ingest.vendor_b_transactions" ;;
        vendor-c_shipments*) TARGET_TABLE="raw_ingest.vendor_c_shipments" ;;
        customer_*)         TARGET_TABLE="raw_ingest.customer_data" ;;
        product_*)          TARGET_TABLE="raw_ingest.product_catalog" ;;
        *)
            log_error "Cannot determine target table for: $BASENAME"
            exit 1
            ;;
    esac
fi

log_info "Loading $(basename "$INPUT_FILE") -> ${TARGET_TABLE}"

# Get pre-load row count
PRE_COUNT=$(get_table_count "production" "$TARGET_TABLE" 2>/dev/null)
PRE_COUNT=${PRE_COUNT:-0}

# Create staging table
STAGING_TABLE="${TARGET_TABLE}_staging_$$"
HEADER=$(head -1 "$INPUT_FILE")

# Build CREATE TABLE from header (all text columns for staging)
COLS=$(echo "$HEADER" | sed 's/"//g' | awk -F, '{for(i=1;i<=NF;i++) printf "  \"%s\" TEXT%s\n", $i, (i<NF?",":"");}')

run_query "production" "DROP TABLE IF EXISTS ${STAGING_TABLE};"
run_query "production" "CREATE TABLE ${STAGING_TABLE} (
${COLS}
);"

if [[ $? -ne 0 ]]; then
    log_error "Failed to create staging table: ${STAGING_TABLE}"
    exit 1
fi

# Bulk load via COPY
# NOTE: Using cat pipe because COPY FROM requires superuser for server-side files
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "\COPY ${STAGING_TABLE} FROM '${INPUT_FILE}' WITH (FORMAT csv, HEADER true, DELIMITER ',', NULL '')" 2>&1

if [[ $? -ne 0 ]]; then
    log_error "COPY failed for ${INPUT_FILE}"
    run_query "production" "DROP TABLE IF EXISTS ${STAGING_TABLE};"
    exit 1
fi

# Merge staging into target (insert only, no upsert)
# TODO: implement proper upsert logic with deduplication
run_query "production" "INSERT INTO ${TARGET_TABLE} SELECT * FROM ${STAGING_TABLE};"

if [[ $? -ne 0 ]]; then
    log_error "Merge failed: ${STAGING_TABLE} -> ${TARGET_TABLE}"
    run_query "production" "DROP TABLE IF EXISTS ${STAGING_TABLE};"
    exit 1
fi

# Cleanup staging
run_query "production" "DROP TABLE IF EXISTS ${STAGING_TABLE};"

# Verify load
POST_COUNT=$(get_table_count "production" "$TARGET_TABLE" 2>/dev/null)
POST_COUNT=${POST_COUNT:-0}
LOADED_ROWS=$((POST_COUNT - PRE_COUNT))

log_info "Loaded ${LOADED_ROWS} rows into ${TARGET_TABLE} (total: ${POST_COUNT})"

exit 0
