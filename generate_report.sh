#!/bin/bash
# ============================================
# Monthly Report Generator
# Generates summary reports and emails them to stakeholders
# Runs on 1st of each month
# Author: mchen
# Created: 2022-07-15
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/configs/pipeline.env"

REPORT_MONTH="${1:-$(date -d 'last month' +%Y-%m 2>/dev/null || date -v-1m +%Y-%m)}"
REPORT_DIR="${LOG_DIR}/reports"
REPORT_FILE="${REPORT_DIR}/monthly_report_${REPORT_MONTH}.txt"

mkdir -p "$REPORT_DIR"

log_info "Generating monthly report for ${REPORT_MONTH}..."

cat > "$REPORT_FILE" <<EOF
================================================================
        AcmeCorp Data Pipeline - Monthly Report
        Period: ${REPORT_MONTH}
        Generated: $(date)
================================================================

EOF

# Section 1: Data Volume Summary
echo "1. DATA VOLUME SUMMARY" >> "$REPORT_FILE"
echo "========================" >> "$REPORT_FILE"

TABLES=("raw_ingest.vendor_a_orders" "raw_ingest.vendor_a_inventory" "raw_ingest.vendor_b_transactions" "raw_ingest.vendor_c_shipments" "raw_ingest.customer_data" "raw_ingest.product_catalog")

for table in "${TABLES[@]}"; do
    COUNT=$(run_query "reporting" \
        "SELECT COUNT(*) FROM ${table} WHERE _load_date >= '${REPORT_MONTH}-01' AND _load_date < '${REPORT_MONTH}-01'::date + interval '1 month';" 2>/dev/null)
    COUNT=$(echo "$COUNT" | tr -d '[:space:]')
    COUNT=${COUNT:-0}
    printf "  %-45s %'12s rows\n" "$table" "$COUNT" >> "$REPORT_FILE"
done

echo "" >> "$REPORT_FILE"

# Section 2: Pipeline Execution Stats
echo "2. PIPELINE EXECUTION STATS" >> "$REPORT_FILE"
echo "========================" >> "$REPORT_FILE"

# Parse logs for execution stats
ETL_RUNS=$(grep -c "Starting ETL Pipeline" "${LOG_DIR}"/pipeline_${REPORT_MONTH}*.log 2>/dev/null || echo "0")
ETL_FAILURES=$(grep -c "ETL.*FAILED" "${LOG_DIR}"/pipeline_${REPORT_MONTH}*.log 2>/dev/null || echo "0")
ETL_SUCCESS=$((ETL_RUNS - ETL_FAILURES))

echo "  Total ETL Runs:     ${ETL_RUNS}" >> "$REPORT_FILE"
echo "  Successful:         ${ETL_SUCCESS}" >> "$REPORT_FILE"
echo "  Failed:             ${ETL_FAILURES}" >> "$REPORT_FILE"
if [[ $ETL_RUNS -gt 0 ]]; then
    SUCCESS_RATE=$(( ETL_SUCCESS * 100 / ETL_RUNS ))
    echo "  Success Rate:       ${SUCCESS_RATE}%" >> "$REPORT_FILE"
fi

echo "" >> "$REPORT_FILE"

# Section 3: Error Summary
echo "3. ERROR SUMMARY" >> "$REPORT_FILE"
echo "========================" >> "$REPORT_FILE"

grep "\[ERROR\]" "${LOG_DIR}"/pipeline_${REPORT_MONTH}*.log 2>/dev/null | \
    awk -F']' '{print $NF}' | sort | uniq -c | sort -rn | head -20 >> "$REPORT_FILE"

echo "" >> "$REPORT_FILE"

# Section 4: Data Quality Summary
echo "4. DATA QUALITY SUMMARY" >> "$REPORT_FILE"
echo "========================" >> "$REPORT_FILE"

DQ_PASS=$(grep -c "\[PASS\]" "${LOG_DIR}"/dq_report_${REPORT_MONTH}*.txt 2>/dev/null || echo "0")
DQ_WARN=$(grep -c "\[WARN\]" "${LOG_DIR}"/dq_report_${REPORT_MONTH}*.txt 2>/dev/null || echo "0")
DQ_FAIL=$(grep -c "\[FAIL\]" "${LOG_DIR}"/dq_report_${REPORT_MONTH}*.txt 2>/dev/null || echo "0")

echo "  DQ Checks Passed:   ${DQ_PASS}" >> "$REPORT_FILE"
echo "  DQ Warnings:        ${DQ_WARN}" >> "$REPORT_FILE"
echo "  DQ Failures:        ${DQ_FAIL}" >> "$REPORT_FILE"

echo "" >> "$REPORT_FILE"

# Section 5: Storage Usage
echo "5. STORAGE USAGE" >> "$REPORT_FILE"
echo "========================" >> "$REPORT_FILE"

for dir in "$DATA_INPUT_DIR" "$DATA_OUTPUT_DIR" "$DATA_ARCHIVE_DIR" "$LOG_DIR"; do
    if [[ -d "$dir" ]]; then
        SIZE=$(du -sh "$dir" 2>/dev/null | awk '{print $1}')
        printf "  %-40s %s\n" "$dir" "$SIZE" >> "$REPORT_FILE"
    fi
done

echo "" >> "$REPORT_FILE"
echo "================================================================" >> "$REPORT_FILE"
echo "END OF REPORT" >> "$REPORT_FILE"
echo "================================================================" >> "$REPORT_FILE"

log_info "Report generated: $REPORT_FILE"

# Email the report
send_email "[Monthly] Data Pipeline Report - ${REPORT_MONTH}" \
    "$(cat "$REPORT_FILE")" \
    "data-ops@acmecorp.com,management@acmecorp.com"

log_info "Report emailed to stakeholders"
