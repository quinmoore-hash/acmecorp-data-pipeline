#!/bin/bash
# ============================================
# CSV Transformation Script
# Cleans and standardizes CSV data for warehouse loading
# 
# Author: jthompson
# Modified: 2023-05-20 - added phone normalization
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/file_utils.sh"
source "${BASE_DIR}/configs/pipeline.env"

INPUT_FILE="$1"
OUTPUT_FILE="$2"

if [[ -z "$INPUT_FILE" ]] || [[ -z "$OUTPUT_FILE" ]]; then
    echo "Usage: $0 <input_csv> <output_csv>"
    exit 1
fi

check_file "$INPUT_FILE" || exit 1

log_info "Transforming: $(basename "$INPUT_FILE")"

TEMP_FILE=$(mktemp)

# Step 1: Remove BOM if present
sed '1s/^\xEF\xBB\xBF//' "$INPUT_FILE" > "$TEMP_FILE"

# Step 2: Normalize line endings (CRLF -> LF)
sed -i '' 's/\r$//' "$TEMP_FILE" 2>/dev/null || sed -i 's/\r$//' "$TEMP_FILE"

# Step 3: Remove empty lines
sed -i '' '/^[[:space:]]*$/d' "$TEMP_FILE" 2>/dev/null || sed -i '/^[[:space:]]*$/d' "$TEMP_FILE"

# Step 4: Trim whitespace from fields
# This awk monstrosity trims spaces around delimiters
awk -F"${CSV_DELIMITER:-,}" '{
    for(i=1; i<=NF; i++) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i);
        printf "%s%s", $i, (i<NF ? "," : "\n")
    }
}' "$TEMP_FILE" > "${TEMP_FILE}.clean"
mv "${TEMP_FILE}.clean" "$TEMP_FILE"

# Step 5: Standardize date formats (MM/DD/YYYY -> YYYY-MM-DD)
# NOTE: this is brittle - assumes dates are in specific columns
# and doesn't handle all formats. Known to break on European dates.
awk -F, 'BEGIN{OFS=","} NR>1{
    for(i=1;i<=NF;i++){
        if($i ~ /^[0-9]{1,2}\/[0-9]{1,2}\/[0-9]{4}$/){
            split($i,d,"/");
            $i = sprintf("%04d-%02d-%02d", d[3], d[1], d[2])
        }
    }
} {print}' "$TEMP_FILE" > "${TEMP_FILE}.dated"
mv "${TEMP_FILE}.dated" "$TEMP_FILE"

# Step 6: Normalize phone numbers (remove non-digits, add +1 prefix)
awk -F, 'BEGIN{OFS=","} NR>1{
    for(i=1;i<=NF;i++){
        if($i ~ /^[\(]?[0-9]{3}[\)\-\. ]?[0-9]{3}[\-\. ]?[0-9]{4}$/){
            gsub(/[^0-9]/, "", $i);
            $i = "+1" $i
        }
    }
} {print}' "$TEMP_FILE" > "${TEMP_FILE}.phone"
mv "${TEMP_FILE}.phone" "$TEMP_FILE"

# Step 7: Replace NULL-like values with empty strings
sed -i '' 's/\bNULL\b//gI; s/\bN\/A\b//gI; s/\bnone\b//gI; s/\bnil\b//gI' "$TEMP_FILE" 2>/dev/null || \
sed -i 's/\bNULL\b//gI; s/\bN\/A\b//gI; s/\bnone\b//gI; s/\bnil\b//gI' "$TEMP_FILE"

# Step 8: Add metadata columns
awk -v run_date="$(date +%Y-%m-%d)" -v src="$(basename "$INPUT_FILE")" \
    -F, 'BEGIN{OFS=","} 
    NR==1{print $0, "_load_date", "_source_file"} 
    NR>1{print $0, run_date, src}' "$TEMP_FILE" > "$OUTPUT_FILE"

rm -f "$TEMP_FILE"

ROWS=$(count_data_rows "$OUTPUT_FILE")
log_info "Transform complete: $(basename "$OUTPUT_FILE") (${ROWS} rows)"

exit 0
