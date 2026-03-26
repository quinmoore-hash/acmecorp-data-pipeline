#!/bin/bash
# ============================================
# Access Log Parser
# Parses API access logs and generates usage stats
# Used for capacity planning and client billing
# Author: asingh
# Created: 2023-01-20
#
# Reads Apache/nginx-style access logs and produces
# summary CSVs broken down by endpoint and client.
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/configs/pipeline.env"

INPUT_LOG="${1:-/var/log/nginx/access.log}"
OUTPUT_DIR="${2:-${DATA_OUTPUT_DIR}/access_stats}"
DATE_FILTER="${3:-$(date +%d/%b/%Y)}"

mkdir -p "$OUTPUT_DIR"

if [[ ! -f "$INPUT_LOG" ]]; then
    log_error "Access log not found: $INPUT_LOG"
    exit 1
fi

log_info "Parsing access logs: $INPUT_LOG (date: $DATE_FILTER)"

# Filter today's entries
TEMP_FILE=$(mktemp)
grep "$DATE_FILTER" "$INPUT_LOG" > "$TEMP_FILE" 2>/dev/null

TOTAL_REQUESTS=$(wc -l < "$TEMP_FILE")

if [[ $TOTAL_REQUESTS -eq 0 ]]; then
    log_warn "No log entries found for $DATE_FILTER"
    rm -f "$TEMP_FILE"
    exit 0
fi

# Summary by endpoint
log_info "Generating endpoint summary..."
echo "endpoint,method,count,avg_response_time_ms,error_count" > "${OUTPUT_DIR}/endpoint_summary_$(date +%Y%m%d).csv"

awk '{
    # Parse combined log format
    # IP - - [timestamp] "METHOD /path HTTP/x.x" status size "referer" "ua" response_time
    match($0, /"([A-Z]+) ([^ ]+)/, req)
    method = req[1]
    path = req[2]
    
    # Extract status code (field after the closing quote of the request)
    for(i=1; i<=NF; i++) {
        if ($i ~ /^[0-9]{3}$/ && $(i-1) ~ /HTTP/) {
            status = $i
            break
        }
    }
    
    # Normalize path (remove query params and IDs)
    gsub(/\?.*/, "", path)
    gsub(/\/[0-9]+/, "/:id", path)
    
    key = path "|" method
    count[key]++
    if (status >= 400) errors[key]++
    
} END {
    for (k in count) {
        split(k, parts, "|")
        printf "%s,%s,%d,0,%d\n", parts[1], parts[2], count[k], errors[k]+0
    }
}' "$TEMP_FILE" | sort -t, -k3 -rn >> "${OUTPUT_DIR}/endpoint_summary_$(date +%Y%m%d).csv"

# Summary by client IP
log_info "Generating client summary..."
echo "client_ip,request_count,error_count,bandwidth_bytes" > "${OUTPUT_DIR}/client_summary_$(date +%Y%m%d).csv"

awk '{
    ip = $1
    # find status and size
    for(i=1; i<=NF; i++) {
        if ($i ~ /^[0-9]{3}$/) { status=$i; size=$(i+1); break }
    }
    count[ip]++
    bandwidth[ip] += size+0
    if (status+0 >= 400) errors[ip]++
} END {
    for (ip in count) {
        printf "%s,%d,%d,%d\n", ip, count[ip], errors[ip]+0, bandwidth[ip]
    }
}' "$TEMP_FILE" | sort -t, -k2 -rn >> "${OUTPUT_DIR}/client_summary_$(date +%Y%m%d).csv"

# Status code distribution
log_info "Generating status code distribution..."
echo "status_code,count,percentage" > "${OUTPUT_DIR}/status_dist_$(date +%Y%m%d).csv"

awk -v total="$TOTAL_REQUESTS" '{
    for(i=1; i<=NF; i++) {
        if ($i ~ /^[0-9]{3}$/) { codes[$i]++; break }
    }
} END {
    for (code in codes) {
        pct = (codes[code] / total) * 100
        printf "%s,%d,%.2f\n", code, codes[code], pct
    }
}' "$TEMP_FILE" | sort -t, -k1 >> "${OUTPUT_DIR}/status_dist_$(date +%Y%m%d).csv"

rm -f "$TEMP_FILE"

log_info "Access log parsing complete. Total requests: ${TOTAL_REQUESTS}"
log_info "Reports written to: ${OUTPUT_DIR}/"
