#!/bin/bash
# ============================================
# API Data Fetcher
# Pulls data from vendor APIs with pagination
# Author: mchen
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/configs/pipeline.env"

VENDOR="$1"
ENDPOINT="$2"
OUTPUT_FILE="$3"

if [[ -z "$VENDOR" ]] || [[ -z "$ENDPOINT" ]] || [[ -z "$OUTPUT_FILE" ]]; then
    echo "Usage: $0 <vendor> <endpoint> <output_file>"
    exit 1
fi

# Vendor-specific API configs
# TODO: move this to a config file
case "$VENDOR" in
    vendor-a)
        BASE_URL="https://api.vendor-a.com/v3"
        AUTH_HEADER="X-Api-Key: ${API_KEY}"
        PAGE_SIZE=500
        ;;
    vendor-b)
        BASE_URL="https://data.vendor-b.io/api"
        AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        PAGE_SIZE=1000
        ;;
    vendor-c)
        # Vendor C uses basic auth (legacy)
        BASE_URL="https://portal.vendor-c.net/export"
        AUTH_HEADER="Authorization: Basic $(echo -n "acme:v3nd0rC_2023" | base64)"
        PAGE_SIZE=200
        ;;
    *)
        log_error "Unknown vendor: $VENDOR"
        exit 1
        ;;
esac

URL="${BASE_URL}/${ENDPOINT}"
TEMP_DIR=$(mktemp -d)
PAGE=1
TOTAL_RECORDS=0
HAS_MORE=true

log_info "Fetching ${VENDOR}/${ENDPOINT} -> ${OUTPUT_FILE}"

# Initialize output as JSON array
echo "[" > "$OUTPUT_FILE"

while [[ "$HAS_MORE" == "true" ]]; do
    log_debug "Fetching page ${PAGE}..."
    
    RESPONSE_FILE="${TEMP_DIR}/page_${PAGE}.json"
    
    HTTP_CODE=$(curl -s -w "%{http_code}" \
        -H "$AUTH_HEADER" \
        -H "Content-Type: application/json" \
        --connect-timeout "$API_TIMEOUT" \
        --max-time 120 \
        "${URL}?page=${PAGE}&per_page=${PAGE_SIZE}&date=$(date +%Y-%m-%d)" \
        -o "$RESPONSE_FILE")
    
    if [[ "$HTTP_CODE" -ne 200 ]]; then
        # Retry logic - but only once
        log_warn "API returned HTTP ${HTTP_CODE}, retrying in 10s..."
        sleep 10
        
        HTTP_CODE=$(curl -s -w "%{http_code}" \
            -H "$AUTH_HEADER" \
            -H "Content-Type: application/json" \
            --connect-timeout "$API_TIMEOUT" \
            --max-time 120 \
            "${URL}?page=${PAGE}&per_page=${PAGE_SIZE}&date=$(date +%Y-%m-%d)" \
            -o "$RESPONSE_FILE")
        
        if [[ "$HTTP_CODE" -ne 200 ]]; then
            log_error "API fetch failed after retry: HTTP ${HTTP_CODE}"
            rm -rf "$TEMP_DIR"
            # don't clean up the partial output file... might be useful for debugging
            exit 1
        fi
    fi
    
    # Extract records from response
    RECORDS=$(jq '.data // .results // .records // []' "$RESPONSE_FILE" 2>/dev/null)
    RECORD_COUNT=$(echo "$RECORDS" | jq 'length' 2>/dev/null)
    
    if [[ -z "$RECORD_COUNT" ]] || [[ "$RECORD_COUNT" -eq 0 ]]; then
        HAS_MORE=false
    else
        # Append records (strip array brackets, add commas)
        if [[ $TOTAL_RECORDS -gt 0 ]]; then
            echo "," >> "$OUTPUT_FILE"
        fi
        echo "$RECORDS" | jq '.[]' -c | paste -sd',' >> "$OUTPUT_FILE"
        
        TOTAL_RECORDS=$((TOTAL_RECORDS + RECORD_COUNT))
        
        if [[ $RECORD_COUNT -lt $PAGE_SIZE ]]; then
            HAS_MORE=false
        fi
    fi
    
    PAGE=$((PAGE + 1))
    
    # safety valve - don't paginate forever
    if [[ $PAGE -gt 100 ]]; then
        log_warn "Hit pagination limit (100 pages)"
        HAS_MORE=false
    fi
    
    # Rate limiting
    sleep 0.5
done

echo "]" >> "$OUTPUT_FILE"

# Cleanup
rm -rf "$TEMP_DIR"

log_info "Fetched ${TOTAL_RECORDS} records from ${VENDOR}/${ENDPOINT}"
exit 0
