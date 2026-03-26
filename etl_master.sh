#!/bin/bash
# ============================================
# ETL Master Orchestrator
# ============================================
# Primary pipeline orchestration script. Runs nightly at 2:00 AM EST.
# Coordinates data ingestion from multiple sources, transformation,
# and loading into the data warehouse.
#
# Author: jthompson
# Created: 2020-06-15
# Modified: 2023-11-02 (added vendor-C feed)
#
# CRON: 0 2 * * * /opt/acmecorp/pipeline/scripts/etl_master.sh >> /var/log/acmecorp/pipeline/etl_master.log 2>&1
#
# Dependencies:
#   - utils/logging.sh, utils/notify.sh, utils/file_utils.sh, utils/db_helpers.sh
#   - scripts/fetch_api_data.sh, scripts/transform_csv.sh, scripts/load_warehouse.sh
#   - psql, curl, jq, aws cli
# ============================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# Source dependencies
source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/file_utils.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/utils/lock_manager.sh"

# Load environment
source "${BASE_DIR}/configs/pipeline.env"

# Job name for locking
JOB_NAME="etl_master"
RUN_ID="ETL_$(date +%Y%m%d_%H%M%S)"
START_TIME=$(date +%s)

# Tracking
STEPS_COMPLETED=0
STEPS_FAILED=0
TOTAL_ROWS_PROCESSED=0
ERRORS=()

# ============================================
# Functions
# ============================================

cleanup() {
    local exit_code=$?
    local end_time=$(date +%s)
    local duration=$(( end_time - START_TIME ))
    
    log_info "=========================================="
    log_info "ETL Run Summary: ${RUN_ID}"
    log_info "Duration: ${duration}s"
    log_info "Steps Completed: ${STEPS_COMPLETED}"
    log_info "Steps Failed: ${STEPS_FAILED}"
    log_info "Total Rows: ${TOTAL_ROWS_PROCESSED}"
    log_info "=========================================="
    
    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        local error_summary=$(printf '%s\n' "${ERRORS[@]}")
        alert "ETL ${RUN_ID} completed with errors:\n${error_summary}\nDuration: ${duration}s" "WARNING"
    elif [[ $exit_code -ne 0 ]]; then
        alert "ETL ${RUN_ID} FAILED (exit=${exit_code})\nDuration: ${duration}s\nSteps completed: ${STEPS_COMPLETED}" "CRITICAL"
    else
        log_info "ETL ${RUN_ID} completed successfully"
        send_slack "ETL ${RUN_ID} completed OK. ${TOTAL_ROWS_PROCESSED} rows in ${duration}s." "INFO"
    fi
    
    cleanup_lock "$JOB_NAME"
    exit $exit_code
}

trap cleanup EXIT

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check required tools
    for cmd in curl jq aws psql awk sed; do
        if ! command -v $cmd &>/dev/null; then
            log_error "Required command not found: $cmd"
            return 1
        fi
    done
    
    # Check database connectivity
    check_db_connection "production" || return 1
    
    # Check disk space (need at least 5GB free)
    local free_space_kb=$(df -k "$DATA_INPUT_DIR" | tail -1 | awk '{print $4}')
    local min_space_kb=5242880
    if [[ $free_space_kb -lt $min_space_kb ]]; then
        log_error "Insufficient disk space: ${free_space_kb}KB free (need ${min_space_kb}KB)"
        return 1
    fi
    
    # Create working directories
    for dir in "$DATA_INPUT_DIR" "$DATA_OUTPUT_DIR" "$DATA_ARCHIVE_DIR" "$DATA_STAGING_DIR" "$DATA_ERROR_DIR"; do
        mkdir -p "$dir" 2>/dev/null
    done
    
    log_info "Prerequisites check passed"
    return 0
}

# Step 1: Fetch data from APIs
step_fetch_api_data() {
    log_info "[Step 1/6] Fetching API data..."
    
    local sources=("vendor-a:orders" "vendor-a:inventory" "vendor-b:transactions" "vendor-c:shipments")
    local fetch_errors=0
    
    for source in "${sources[@]}"; do
        local vendor=$(echo "$source" | cut -d: -f1)
        local endpoint=$(echo "$source" | cut -d: -f2)
        
        log_info "Fetching ${vendor}/${endpoint}..."
        
        "${SCRIPT_DIR}/fetch_api_data.sh" "$vendor" "$endpoint" "${DATA_INPUT_DIR}/${vendor}_${endpoint}_$(date +%Y%m%d).json"
        
        if [[ $? -ne 0 ]]; then
            log_error "Failed to fetch ${vendor}/${endpoint}"
            ERRORS+=("FETCH: ${vendor}/${endpoint} failed")
            fetch_errors=$((fetch_errors + 1))
        fi
    done
    
    if [[ $fetch_errors -gt 0 ]]; then
        log_warn "API fetch completed with ${fetch_errors} errors"
        return 1
    fi
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 1/6] API fetch complete"
    return 0
}

# Step 2: Collect file drops
step_collect_file_drops() {
    log_info "[Step 2/6] Collecting file drops..."
    
    # Check for CSV files from legacy FTP
    local ftp_dir="/opt/acmecorp/data/ftp_incoming"
    local csv_count=$(find "$ftp_dir" -name "*.csv" -newer "$ftp_dir/.last_pickup" 2>/dev/null | wc -l)
    
    if [[ $csv_count -gt 0 ]]; then
        log_info "Found ${csv_count} new CSV files from FTP"
        find "$ftp_dir" -name "*.csv" -newer "$ftp_dir/.last_pickup" 2>/dev/null | while read csvfile; do
            local basename=$(basename "$csvfile")
            cp "$csvfile" "${DATA_INPUT_DIR}/${basename}"
            log_info "Collected: $basename"
        done
        touch "$ftp_dir/.last_pickup"
    else
        log_info "No new FTP file drops"
    fi
    
    # Check for S3 file drops
    log_info "Checking S3 for new files..."
    local s3_files=$(aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/$(date +%Y/%m/%d)/" --profile "$AWS_PROFILE" 2>/dev/null | awk '{print $4}')
    
    for s3file in $s3_files; do
        if [[ -n "$s3file" ]]; then
            aws s3 cp "s3://${S3_BUCKET}/${S3_PREFIX}/$(date +%Y/%m/%d)/${s3file}" \
                "${DATA_INPUT_DIR}/${s3file}" \
                --profile "$AWS_PROFILE" 2>/dev/null
            log_info "Downloaded from S3: $s3file"
        fi
    done
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 2/6] File collection complete"
    return 0
}

# Step 3: Validate incoming data
step_validate_data() {
    log_info "[Step 3/6] Validating incoming data..."
    
    local validation_errors=0
    
    # Validate CSVs
    for csvfile in "${DATA_INPUT_DIR}"/*.csv; do
        [[ -f "$csvfile" ]] || continue
        validate_csv "$csvfile"
        if [[ $? -ne 0 ]]; then
            log_error "Validation failed: $csvfile"
            move_file "$csvfile" "${DATA_ERROR_DIR}/"
            validation_errors=$((validation_errors + 1))
            ERRORS+=("VALIDATE: $(basename "$csvfile") failed validation")
        fi
    done
    
    # Validate JSONs
    for jsonfile in "${DATA_INPUT_DIR}"/*.json; do
        [[ -f "$jsonfile" ]] || continue
        validate_json "$jsonfile"
        if [[ $? -ne 0 ]]; then
            log_error "Validation failed: $jsonfile"
            move_file "$jsonfile" "${DATA_ERROR_DIR}/"
            validation_errors=$((validation_errors + 1))
            ERRORS+=("VALIDATE: $(basename "$jsonfile") failed validation")
        fi
    done
    
    if [[ $validation_errors -gt 0 ]]; then
        log_warn "Validation completed with ${validation_errors} errors"
    fi
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 3/6] Validation complete"
    return 0
}

# Step 4: Transform data
step_transform() {
    log_info "[Step 4/6] Transforming data..."
    
    # Transform CSVs
    for csvfile in "${DATA_INPUT_DIR}"/*.csv; do
        [[ -f "$csvfile" ]] || continue
        local outfile="${DATA_STAGING_DIR}/$(basename "${csvfile%.*}")_transformed.csv"
        
        "${SCRIPT_DIR}/transform_csv.sh" "$csvfile" "$outfile"
        if [[ $? -eq 0 ]]; then
            local row_count=$(count_data_rows "$outfile")
            TOTAL_ROWS_PROCESSED=$((TOTAL_ROWS_PROCESSED + row_count))
            log_info "Transformed: $(basename "$csvfile") (${row_count} rows)"
        else
            ERRORS+=("TRANSFORM: $(basename "$csvfile") failed")
            STEPS_FAILED=$((STEPS_FAILED + 1))
        fi
    done
    
    # Transform JSON to CSV (for warehouse loading)
    for jsonfile in "${DATA_INPUT_DIR}"/*.json; do
        [[ -f "$jsonfile" ]] || continue
        local outfile="${DATA_STAGING_DIR}/$(basename "${jsonfile%.*}")_transformed.csv"
        
        "${SCRIPT_DIR}/json_to_csv.sh" "$jsonfile" "$outfile"
        if [[ $? -eq 0 ]]; then
            local row_count=$(count_data_rows "$outfile")
            TOTAL_ROWS_PROCESSED=$((TOTAL_ROWS_PROCESSED + row_count))
            log_info "Converted: $(basename "$jsonfile") -> CSV (${row_count} rows)"
        else
            ERRORS+=("TRANSFORM: $(basename "$jsonfile") failed")
            STEPS_FAILED=$((STEPS_FAILED + 1))
        fi
    done
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 4/6] Transform complete. Total rows: ${TOTAL_ROWS_PROCESSED}"
    return 0
}

# Step 5: Load into warehouse
step_load() {
    log_info "[Step 5/6] Loading data into warehouse..."
    
    for datafile in "${DATA_STAGING_DIR}"/*_transformed.csv; do
        [[ -f "$datafile" ]] || continue
        
        "${SCRIPT_DIR}/load_warehouse.sh" "$datafile"
        if [[ $? -ne 0 ]]; then
            ERRORS+=("LOAD: $(basename "$datafile") failed")
            STEPS_FAILED=$((STEPS_FAILED + 1))
        else
            log_info "Loaded: $(basename "$datafile")"
        fi
    done
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 5/6] Warehouse load complete"
    return 0
}

# Step 6: Archive and cleanup
step_archive() {
    log_info "[Step 6/6] Archiving processed files..."
    
    # Archive input files
    for datafile in "${DATA_INPUT_DIR}"/*.{csv,json}; do
        [[ -f "$datafile" ]] || continue
        archive_file "$datafile" "$DATA_ARCHIVE_DIR"
        rm -f "$datafile"
    done
    
    # Clean staging
    rm -f "${DATA_STAGING_DIR}"/*_transformed.csv
    
    # Upload archive to S3 for long-term storage
    aws s3 sync "$DATA_ARCHIVE_DIR" \
        "s3://${S3_BUCKET}/archive/$(date +%Y/%m/%d)/" \
        --profile "$AWS_PROFILE" \
        --quiet 2>/dev/null
    
    # Cleanup old archives (keep 30 days locally)
    find "$DATA_ARCHIVE_DIR" -type f -mtime +30 -delete 2>/dev/null
    
    STEPS_COMPLETED=$((STEPS_COMPLETED + 1))
    log_info "[Step 6/6] Archive complete"
    return 0
}

# ============================================
# Main Execution
# ============================================

main() {
    log_info "=========================================="
    log_info "Starting ETL Pipeline: ${RUN_ID}"
    log_info "Host: $(hostname)"
    log_info "Date: $(date)"
    log_info "=========================================="
    
    # Acquire exclusive lock
    acquire_lock "$JOB_NAME" 300 || {
        alert "ETL ${RUN_ID}: Could not acquire lock. Another instance may be running." "CRITICAL"
        exit 1
    }
    
    # Pre-flight checks
    check_prerequisites || {
        alert "ETL ${RUN_ID}: Prerequisites check failed" "CRITICAL"
        exit 1
    }
    
    # Execute pipeline steps
    step_fetch_api_data
    # Don't fail the whole pipeline if API fetch has issues
    # the file drops might still work
    
    step_collect_file_drops || {
        alert "ETL ${RUN_ID}: File collection failed" "CRITICAL"
        exit 1
    }
    
    step_validate_data
    # validation is advisory, continue even with errors
    
    step_transform || {
        alert "ETL ${RUN_ID}: Transform step failed" "CRITICAL"
        exit 1
    }
    
    step_load || {
        alert "ETL ${RUN_ID}: Warehouse load failed" "CRITICAL"
        exit 1
    }
    
    step_archive
    
    # Run post-load data quality checks
    log_info "Running post-load validation..."
    "${SCRIPT_DIR}/data_quality_check.sh" "${RUN_ID}" || {
        ERRORS+=("POST-LOAD: Data quality check had warnings")
    }
    
    if [[ ${STEPS_FAILED} -gt 0 ]]; then
        exit 1
    fi
    
    exit 0
}

main "$@"
