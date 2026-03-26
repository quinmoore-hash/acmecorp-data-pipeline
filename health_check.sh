#!/bin/bash
# ============================================
# System Health Check
# Runs every 5 minutes, checks core dependencies
# Author: asingh
# No error handling on purpose - this is a quick check
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/utils/db_helpers.sh"
source "${BASE_DIR}/configs/pipeline.env"

STATUS_FILE="/tmp/acmecorp_health_status"
LAST_FAIL_FILE="/tmp/acmecorp_health_last_fail"

CHECKS_TOTAL=0
CHECKS_OK=0
CHECKS_FAIL=0
FAILURES=""

check() {
    local name="$1"
    local cmd="$2"
    CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
    
    eval "$cmd" > /dev/null 2>&1
    if [[ $? -eq 0 ]]; then
        CHECKS_OK=$((CHECKS_OK + 1))
        echo "OK   $name" >> "$STATUS_FILE"
    else
        CHECKS_FAIL=$((CHECKS_FAIL + 1))
        FAILURES="${FAILURES}  - ${name}\n"
        echo "FAIL $name" >> "$STATUS_FILE"
    fi
}

# Reset status file
echo "Health Check: $(date)" > "$STATUS_FILE"
echo "========================" >> "$STATUS_FILE"

# Database connectivity
check "db_production" "check_db_connection production"
check "db_reporting" "check_db_connection reporting"

# API endpoints
check "api_vendor_a" "curl -sf --connect-timeout 5 https://api.vendor-a.com/v3/health"
check "api_vendor_b" "curl -sf --connect-timeout 5 https://data.vendor-b.io/api/ping"

# Disk space (>10% free)
check "disk_data" "test \$(df ${DATA_INPUT_DIR} 2>/dev/null | tail -1 | awk '{print 100-\$5}' | tr -d '%') -gt 10"
check "disk_logs" "test \$(df ${LOG_DIR} 2>/dev/null | tail -1 | awk '{print 100-\$5}' | tr -d '%') -gt 10"

# S3 access
check "s3_bucket" "aws s3 ls s3://${S3_BUCKET}/ --profile ${AWS_PROFILE} --max-items 1"

# Required tools
check "tool_psql" "command -v psql"
check "tool_jq" "command -v jq"
check "tool_aws" "command -v aws"
check "tool_curl" "command -v curl"

# Check if critical directories exist
check "dir_input" "test -d ${DATA_INPUT_DIR}"
check "dir_output" "test -d ${DATA_OUTPUT_DIR}"
check "dir_logs" "test -d ${LOG_DIR}"

echo "========================" >> "$STATUS_FILE"
echo "Total: ${CHECKS_TOTAL} | OK: ${CHECKS_OK} | FAIL: ${CHECKS_FAIL}" >> "$STATUS_FILE"

# Alert on failures
if [[ $CHECKS_FAIL -gt 0 ]]; then
    # Only alert if this is a new failure (avoid repeated alerts)
    CURRENT_HASH=$(echo "$FAILURES" | md5sum | awk '{print $1}')
    LAST_HASH=$(cat "$LAST_FAIL_FILE" 2>/dev/null)
    
    if [[ "$CURRENT_HASH" != "$LAST_HASH" ]]; then
        alert "Health Check: ${CHECKS_FAIL}/${CHECKS_TOTAL} checks failed:\n${FAILURES}" "WARNING"
        echo "$CURRENT_HASH" > "$LAST_FAIL_FILE"
    fi
else
    rm -f "$LAST_FAIL_FILE"
fi

log_debug "Health check: ${CHECKS_OK}/${CHECKS_TOTAL} OK"
