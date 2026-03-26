#!/bin/bash
# ============================================
# Data Quality Check Framework
# ============================================
# Runs post-load validation checks against the warehouse.
# Checks for row count anomalies, null rates, duplicates,
# freshness, and business rule violations.
#
# Author: jthompson
# Created: 2021-09-10
# Modified: 2024-01-15 (added vendor-c checks, SLA tracking)
#
# Usage: ./data_quality_check.sh [run_id]
# Called by: etl_master.sh (step 6)
#
# Exit codes:
#   0 = all checks passed
#   1 = critical check failed
#   2 = warnings only
# ============================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/configs/pipeline.env"

RUN_ID="${1:-DQ_$(date +%Y%m%d_%H%M%S)}"
CHECK_DATE=$(date +%Y-%m-%d)
REPORT_FILE="${LOG_DIR}/dq_report_$(date +%Y%m%d).txt"

# Counters
CHECKS_RUN=0
CHECKS_PASSED=0
CHECKS_WARNED=0
CHECKS_FAILED=0

# Deviation threshold from config
ROW_DEVIATION_PCT="${ROW_COUNT_DEVIATION_PCT:-20}"

# ============================================
# Check Functions
# ============================================

record_result() {
    local check_name="$1"
    local status="$2"  # PASS, WARN, FAIL
    local details="$3"
    
    CHECKS_RUN=$((CHECKS_RUN + 1))
    
    case "$status" in
        PASS) CHECKS_PASSED=$((CHECKS_PASSED + 1)) ;;
        WARN) CHECKS_WARNED=$((CHECKS_WARNED + 1)) ;;
        FAIL) CHECKS_FAILED=$((CHECKS_FAILED + 1)) ;;
    esac
    
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${status}] ${check_name}: ${details}" >> "$REPORT_FILE"
    log_info "DQ [${status}] ${check_name}: ${details}"
}

# Check 1: Row count anomaly detection
check_row_counts() {
    log_info "Running row count checks..."
    
    local tables=(
        "raw_ingest.vendor_a_orders"
        "raw_ingest.vendor_a_inventory"
        "raw_ingest.vendor_b_transactions"
        "raw_ingest.vendor_c_shipments"
        "raw_ingest.customer_data"
        "raw_ingest.product_catalog"
    )
    
    for table in "${tables[@]}"; do
        # Get today's count
        local today_count=$(run_query "production" \
            "SELECT COUNT(*) FROM ${table} WHERE _load_date = '${CHECK_DATE}';" 2>/dev/null)
        today_count=$(echo "$today_count" | tr -d '[:space:]')
        today_count=${today_count:-0}
        
        # Get 7-day average
        local avg_count=$(run_query "production" \
            "SELECT COALESCE(ROUND(AVG(cnt)), 0) FROM (
                SELECT _load_date, COUNT(*) as cnt 
                FROM ${table} 
                WHERE _load_date >= '${CHECK_DATE}'::date - interval '7 days'
                  AND _load_date < '${CHECK_DATE}'
                GROUP BY _load_date
            ) t;" 2>/dev/null)
        avg_count=$(echo "$avg_count" | tr -d '[:space:]')
        avg_count=${avg_count:-0}
        
        if [[ $avg_count -eq 0 ]]; then
            if [[ $today_count -eq 0 ]]; then
                record_result "row_count:${table}" "WARN" "No data today and no historical baseline"
            else
                record_result "row_count:${table}" "PASS" "First load: ${today_count} rows"
            fi
            continue
        fi
        
        # Calculate deviation
        local deviation=0
        if [[ $avg_count -gt 0 ]]; then
            deviation=$(( (today_count - avg_count) * 100 / avg_count ))
            # absolute value
            [[ $deviation -lt 0 ]] && deviation=$(( -deviation ))
        fi
        
        if [[ $deviation -gt $ROW_DEVIATION_PCT ]]; then
            if [[ $today_count -eq 0 ]]; then
                record_result "row_count:${table}" "FAIL" \
                    "ZERO rows today (avg: ${avg_count}). Possible data feed failure."
            else
                record_result "row_count:${table}" "WARN" \
                    "Row count deviation ${deviation}% (today: ${today_count}, avg: ${avg_count})"
            fi
        else
            record_result "row_count:${table}" "PASS" \
                "Row count OK (today: ${today_count}, avg: ${avg_count}, dev: ${deviation}%)"
        fi
    done
}

# Check 2: Null rate checks
check_null_rates() {
    log_info "Running null rate checks..."
    
    # Critical columns that should never be null
    local checks=(
        "raw_ingest.vendor_a_orders:order_id:0"
        "raw_ingest.vendor_a_orders:customer_id:0"
        "raw_ingest.vendor_a_orders:order_date:0"
        "raw_ingest.vendor_a_orders:total_amount:5"
        "raw_ingest.vendor_b_transactions:transaction_id:0"
        "raw_ingest.vendor_b_transactions:amount:0"
        "raw_ingest.vendor_c_shipments:shipment_id:0"
        "raw_ingest.vendor_c_shipments:tracking_number:10"
        "raw_ingest.customer_data:email:5"
    )
    
    for check in "${checks[@]}"; do
        local table=$(echo "$check" | cut -d: -f1)
        local column=$(echo "$check" | cut -d: -f2)
        local max_null_pct=$(echo "$check" | cut -d: -f3)
        
        local result=$(run_query "production" \
            "SELECT ROUND(100.0 * SUM(CASE WHEN \"${column}\" IS NULL OR \"${column}\" = '' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2)
             FROM ${table} 
             WHERE _load_date = '${CHECK_DATE}';" 2>/dev/null)
        result=$(echo "$result" | tr -d '[:space:]')
        result=${result:-0}
        
        # Compare as integers (bash doesn't do float comparison natively)
        local null_pct_int=$(echo "$result" | cut -d. -f1)
        null_pct_int=${null_pct_int:-0}
        
        if [[ $null_pct_int -gt $max_null_pct ]]; then
            record_result "null_rate:${table}.${column}" "FAIL" \
                "Null rate ${result}% exceeds threshold ${max_null_pct}%"
        else
            record_result "null_rate:${table}.${column}" "PASS" \
                "Null rate ${result}% within threshold ${max_null_pct}%"
        fi
    done
}

# Check 3: Duplicate detection
check_duplicates() {
    log_info "Running duplicate checks..."
    
    local checks=(
        "raw_ingest.vendor_a_orders:order_id"
        "raw_ingest.vendor_b_transactions:transaction_id"
        "raw_ingest.vendor_c_shipments:shipment_id"
    )
    
    for check in "${checks[@]}"; do
        local table=$(echo "$check" | cut -d: -f1)
        local key_col=$(echo "$check" | cut -d: -f2)
        
        local dup_count=$(run_query "production" \
            "SELECT COUNT(*) FROM (
                SELECT \"${key_col}\", COUNT(*) 
                FROM ${table}
                WHERE _load_date = '${CHECK_DATE}'
                GROUP BY \"${key_col}\"
                HAVING COUNT(*) > 1
            ) t;" 2>/dev/null)
        dup_count=$(echo "$dup_count" | tr -d '[:space:]')
        dup_count=${dup_count:-0}
        
        if [[ $dup_count -gt 0 ]]; then
            record_result "duplicates:${table}.${key_col}" "WARN" \
                "${dup_count} duplicate keys found"
        else
            record_result "duplicates:${table}.${key_col}" "PASS" \
                "No duplicates found"
        fi
    done
}

# Check 4: Data freshness
check_freshness() {
    log_info "Running freshness checks..."
    
    local tables=(
        "raw_ingest.vendor_a_orders"
        "raw_ingest.vendor_b_transactions"
        "raw_ingest.vendor_c_shipments"
    )
    
    for table in "${tables[@]}"; do
        local has_today=$(run_query "production" \
            "SELECT EXISTS(SELECT 1 FROM ${table} WHERE _load_date = '${CHECK_DATE}');" 2>/dev/null)
        has_today=$(echo "$has_today" | tr -d '[:space:]')
        
        if [[ "$has_today" == "t" ]] || [[ "$has_today" == "true" ]]; then
            record_result "freshness:${table}" "PASS" "Data loaded today"
        else
            # check when last load was
            local last_load=$(run_query "production" \
                "SELECT MAX(_load_date) FROM ${table};" 2>/dev/null)
            last_load=$(echo "$last_load" | tr -d '[:space:]')
            record_result "freshness:${table}" "WARN" \
                "No data today. Last load: ${last_load:-never}"
        fi
    done
}

# Check 5: Business rules
check_business_rules() {
    log_info "Running business rule checks..."
    
    # Orders should have positive amounts
    local neg_amounts=$(run_query "production" \
        "SELECT COUNT(*) FROM raw_ingest.vendor_a_orders 
         WHERE _load_date = '${CHECK_DATE}' 
         AND total_amount::numeric < 0;" 2>/dev/null)
    neg_amounts=$(echo "$neg_amounts" | tr -d '[:space:]')
    neg_amounts=${neg_amounts:-0}
    
    if [[ $neg_amounts -gt 0 ]]; then
        record_result "biz_rule:negative_amounts" "WARN" \
            "${neg_amounts} orders with negative amounts"
    else
        record_result "biz_rule:negative_amounts" "PASS" "No negative amounts"
    fi
    
    # Shipment dates should not be in the future
    local future_dates=$(run_query "production" \
        "SELECT COUNT(*) FROM raw_ingest.vendor_c_shipments
         WHERE _load_date = '${CHECK_DATE}'
         AND ship_date::date > CURRENT_DATE;" 2>/dev/null)
    future_dates=$(echo "$future_dates" | tr -d '[:space:]')
    future_dates=${future_dates:-0}
    
    if [[ $future_dates -gt 0 ]]; then
        record_result "biz_rule:future_ship_dates" "WARN" \
            "${future_dates} shipments with future dates"
    else
        record_result "biz_rule:future_ship_dates" "PASS" "No future ship dates"
    fi
    
    # Transaction amounts should be within reasonable range
    local outliers=$(run_query "production" \
        "SELECT COUNT(*) FROM raw_ingest.vendor_b_transactions
         WHERE _load_date = '${CHECK_DATE}'
         AND ABS(amount::numeric) > 1000000;" 2>/dev/null)
    outliers=$(echo "$outliers" | tr -d '[:space:]')
    outliers=${outliers:-0}
    
    if [[ $outliers -gt 0 ]]; then
        record_result "biz_rule:amount_outliers" "WARN" \
            "${outliers} transactions over $1M threshold"
    else
        record_result "biz_rule:amount_outliers" "PASS" "No amount outliers"
    fi
}

# ============================================
# Report Generation
# ============================================

generate_report() {
    echo "" >> "$REPORT_FILE"
    echo "=========================================" >> "$REPORT_FILE"
    echo "Data Quality Summary - ${RUN_ID}" >> "$REPORT_FILE"
    echo "Date: ${CHECK_DATE}" >> "$REPORT_FILE"
    echo "=========================================" >> "$REPORT_FILE"
    echo "Total Checks:  ${CHECKS_RUN}" >> "$REPORT_FILE"
    echo "Passed:        ${CHECKS_PASSED}" >> "$REPORT_FILE"
    echo "Warnings:      ${CHECKS_WARNED}" >> "$REPORT_FILE"
    echo "Failed:        ${CHECKS_FAILED}" >> "$REPORT_FILE"
    echo "=========================================" >> "$REPORT_FILE"
    
    log_info "DQ Report written to: ${REPORT_FILE}"
}

# ============================================
# Main
# ============================================

main() {
    log_info "Starting Data Quality Checks: ${RUN_ID}"
    
    echo "=========================================" > "$REPORT_FILE"
    echo "Data Quality Report - ${RUN_ID}" >> "$REPORT_FILE"
    echo "Generated: $(date)" >> "$REPORT_FILE"
    echo "=========================================" >> "$REPORT_FILE"
    
    # Run all checks
    check_row_counts
    check_null_rates
    check_duplicates
    check_freshness
    check_business_rules
    
    # Generate summary
    generate_report
    
    # Alerting based on results
    if [[ $CHECKS_FAILED -gt 0 ]]; then
        alert "DQ Check ${RUN_ID}: ${CHECKS_FAILED} FAILED checks out of ${CHECKS_RUN}. See ${REPORT_FILE}" "CRITICAL"
        exit 1
    elif [[ $CHECKS_WARNED -gt 0 ]]; then
        send_slack "DQ Check ${RUN_ID}: ${CHECKS_WARNED} warnings out of ${CHECKS_RUN} checks. See ${REPORT_FILE}" "WARNING"
        exit 2
    else
        log_info "All ${CHECKS_RUN} data quality checks passed"
        exit 0
    fi
}

main "$@"
