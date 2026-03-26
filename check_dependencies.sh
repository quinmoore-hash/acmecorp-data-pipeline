#!/bin/bash
# ============================================
# Dependency Checker
# Verifies all required tools and services are available
# Useful for debugging environment issues
# Author: jthompson
# ============================================

echo "============================================"
echo "AcmeCorp Pipeline - Dependency Check"
echo "Host: $(hostname)"
echo "Date: $(date)"
echo "User: $(whoami)"
echo "============================================"
echo ""

ERRORS=0
WARNINGS=0

check_tool() {
    local tool="$1"
    local required="$2"  # "required" or "optional"
    
    if command -v "$tool" &>/dev/null; then
        local version=$($tool --version 2>&1 | head -1)
        printf "  ✓ %-15s %s\n" "$tool" "$version"
    else
        if [[ "$required" == "required" ]]; then
            printf "  ✗ %-15s NOT FOUND (required)\n" "$tool"
            ERRORS=$((ERRORS + 1))
        else
            printf "  ? %-15s NOT FOUND (optional)\n" "$tool"
            WARNINGS=$((WARNINGS + 1))
        fi
    fi
}

echo "1. Required Tools"
echo "-------------------"
check_tool "bash" "required"
check_tool "curl" "required"
check_tool "jq" "required"
check_tool "aws" "required"
check_tool "psql" "required"
check_tool "awk" "required"
check_tool "sed" "required"
check_tool "grep" "required"
check_tool "gzip" "required"

echo ""
echo "2. Optional Tools"
echo "-------------------"
check_tool "python3" "optional"
check_tool "ftp" "optional"
check_tool "mailx" "optional"
check_tool "sendmail" "optional"
check_tool "iconv" "optional"
check_tool "bc" "optional"
check_tool "pg_dump" "optional"
check_tool "pg_restore" "optional"

echo ""
echo "3. Directory Structure"
echo "-------------------"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
source "${BASE_DIR}/configs/pipeline.env" 2>/dev/null

DIRS=(
    "$DATA_INPUT_DIR"
    "$DATA_OUTPUT_DIR"
    "$DATA_ARCHIVE_DIR"
    "$DATA_STAGING_DIR"
    "$DATA_ERROR_DIR"
    "$LOG_DIR"
    "/opt/acmecorp/backups/database"
    "/opt/acmecorp/data/ftp_incoming"
)

for dir in "${DIRS[@]}"; do
    if [[ -d "$dir" ]]; then
        printf "  ✓ %-45s exists\n" "$dir"
    else
        printf "  ✗ %-45s MISSING\n" "$dir"
        WARNINGS=$((WARNINGS + 1))
    fi
done

echo ""
echo "4. Configuration Files"
echo "-------------------"

CONFIGS=(
    "${BASE_DIR}/configs/pipeline.env"
    "${BASE_DIR}/configs/database.conf"
    "${BASE_DIR}/configs/alerting.conf"
)

for conf in "${CONFIGS[@]}"; do
    if [[ -f "$conf" ]]; then
        printf "  ✓ %-45s OK\n" "$(basename "$conf")"
    else
        printf "  ✗ %-45s MISSING\n" "$(basename "$conf")"
        ERRORS=$((ERRORS + 1))
    fi
done

echo ""
echo "5. Network Connectivity"
echo "-------------------"

check_url() {
    local name="$1"
    local url="$2"
    
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null)
    if [[ "$HTTP_CODE" -ge 200 ]] && [[ "$HTTP_CODE" -lt 500 ]]; then
        printf "  ✓ %-30s HTTP %s\n" "$name" "$HTTP_CODE"
    else
        printf "  ✗ %-30s UNREACHABLE (HTTP %s)\n" "$name" "$HTTP_CODE"
        WARNINGS=$((WARNINGS + 1))
    fi
}

check_url "Vendor A API" "https://api.vendor-a.com/v3/health"
check_url "Vendor B API" "https://data.vendor-b.io/api/ping"
check_url "Slack Webhook" "https://hooks.slack.com"
check_url "PagerDuty" "https://events.pagerduty.com"

echo ""
echo "6. Disk Space"
echo "-------------------"
df -h / /opt /var /tmp 2>/dev/null | awk 'NR==1 || /^\//' | column -t
echo ""

echo "============================================"
echo "Summary: ${ERRORS} errors, ${WARNINGS} warnings"
echo "============================================"

if [[ $ERRORS -gt 0 ]]; then
    echo "RESULT: FAIL - fix required errors before running pipeline"
    exit 1
else
    echo "RESULT: OK"
    exit 0
fi
