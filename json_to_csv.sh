#!/bin/bash
# Convert JSON data files to CSV for warehouse loading
# Handles nested JSON by flattening top-level keys
# Author: mchen
# HACK: this script is fragile with deeply nested JSON

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/file_utils.sh"

INPUT_FILE="$1"
OUTPUT_FILE="$2"

if [[ -z "$INPUT_FILE" ]] || [[ -z "$OUTPUT_FILE" ]]; then
    echo "Usage: $0 <input_json> <output_csv>"
    exit 1
fi

check_file "$INPUT_FILE" || exit 1

log_info "Converting JSON to CSV: $(basename "$INPUT_FILE")"

# Check if jq is available
if ! command -v jq &>/dev/null; then
    log_error "jq is required but not installed"
    exit 1
fi

# Determine if array or single object
IS_ARRAY=$(jq 'if type == "array" then "true" else "false" end' "$INPUT_FILE" 2>/dev/null)

if [[ "$IS_ARRAY" != "true" ]] && [[ "$IS_ARRAY" != "\"true\"" ]]; then
    # Wrap single object in array
    jq '[.]' "$INPUT_FILE" > "${INPUT_FILE}.tmp"
    mv "${INPUT_FILE}.tmp" "${INPUT_FILE}"
fi

# Extract headers from first record
HEADERS=$(jq -r '.[0] | keys_unsorted | @csv' "$INPUT_FILE" 2>/dev/null)

if [[ -z "$HEADERS" ]]; then
    log_error "Could not extract headers from JSON"
    exit 1
fi

# Write header
echo "$HEADERS" > "$OUTPUT_FILE"

# Convert each record to CSV row
# NOTE: this doesn't handle nested objects well - they get stringified
jq -r '.[] | [.[]] | @csv' "$INPUT_FILE" >> "$OUTPUT_FILE" 2>/dev/null

if [[ $? -ne 0 ]]; then
    log_error "JSON to CSV conversion failed"
    exit 1
fi

ROWS=$(wc -l < "$OUTPUT_FILE")
ROWS=$((ROWS - 1))  # exclude header

log_info "Converted $(basename "$INPUT_FILE") -> $(basename "$OUTPUT_FILE") (${ROWS} rows)"
exit 0
