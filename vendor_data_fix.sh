#!/bin/bash
# "Quick fix" script for vendor data issues
# This gets run manually when vendor sends bad data
# Every time there's a new issue, someone adds another sed/awk hack here
# 
# History of fixes:
# 2021-03: vendor-a started sending unicode in names
# 2021-08: vendor-b changed date format without telling us  
# 2022-01: vendor-a added extra column mid-file
# 2022-06: vendor-c sends amounts with currency symbols
# 2023-02: vendor-b sends duplicate headers mid-file
# 2023-09: vendor-a encoding changed to latin-1 randomly
# 2024-01: vendor-c started sending negative quantities as "(123)" instead of "-123"

INPUT="$1"

if [ -z "$INPUT" ]; then
    echo "Usage: $0 <file>"
    echo "Applies known vendor data fixes to the file in-place"
    exit 1
fi

if [ ! -f "$INPUT" ]; then
    echo "File not found: $INPUT"
    exit 1
fi

echo "Applying vendor data fixes to: $INPUT"

# Fix 1: Remove unicode BOM
sed -i '' 's/^\xEF\xBB\xBF//' "$INPUT" 2>/dev/null
sed -i 's/^\xEF\xBB\xBF//' "$INPUT" 2>/dev/null

# Fix 2: Convert CRLF to LF
sed -i '' 's/\r$//' "$INPUT" 2>/dev/null
sed -i 's/\r$//' "$INPUT" 2>/dev/null

# Fix 3: Remove duplicate header rows that appear mid-file
# (vendor-b does this sometimes when their export paginates)
HEADER=$(head -1 "$INPUT")
awk -v header="$HEADER" 'NR==1 || $0 != header' "$INPUT" > "${INPUT}.tmp" && mv "${INPUT}.tmp" "$INPUT"

# Fix 4: Remove currency symbols from amount fields
# Handles $, €, £, ¥ and comma-formatted numbers like $1,234.56
sed -i '' 's/\$//g; s/€//g; s/£//g; s/¥//g' "$INPUT" 2>/dev/null
sed -i 's/\$//g; s/€//g; s/£//g; s/¥//g' "$INPUT" 2>/dev/null

# Fix 5: Convert accounting negative format (123) -> -123
# This regex is not perfect but catches most cases
sed -i '' 's/(\([0-9,.]*\))/-\1/g' "$INPUT" 2>/dev/null
sed -i 's/(\([0-9,.]*\))/-\1/g' "$INPUT" 2>/dev/null

# Fix 6: Remove thousands separators from numbers
# WARNING: this is dangerous if commas are also the CSV delimiter
# Only apply to files that use tab or pipe delimiters
FIRST_LINE=$(head -1 "$INPUT")
if echo "$FIRST_LINE" | grep -q $'\t' || echo "$FIRST_LINE" | grep -q '|'; then
    sed -i '' 's/\([0-9]\),\([0-9]\{3\}\)/\1\2/g' "$INPUT" 2>/dev/null
    sed -i 's/\([0-9]\),\([0-9]\{3\}\)/\1\2/g' "$INPUT" 2>/dev/null
fi

# Fix 7: Normalize date formats
# DD-Mon-YYYY -> YYYY-MM-DD (vendor-c format)
# This is really hacky
python3 -c "
import csv, sys, re
from datetime import datetime

formats = ['%d-%b-%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y%m%d', '%m-%d-%Y']

def try_parse_date(val):
    for fmt in formats:
        try:
            return datetime.strptime(val.strip(), fmt).strftime('%Y-%m-%d')
        except:
            pass
    return val

with open('${INPUT}', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)

with open('${INPUT}', 'w', newline='') as f:
    writer = csv.writer(f)
    for i, row in enumerate(rows):
        if i == 0:
            writer.writerow(row)
        else:
            writer.writerow([try_parse_date(cell) if re.match(r'\d', cell.strip()) and any(c in cell for c in '-/') else cell for cell in row])
" 2>/dev/null

# Fix 8: Remove non-ASCII characters from text fields (vendor-a latin-1 issue)  
# But preserve the data by transliterating
if command -v iconv &>/dev/null; then
    iconv -f utf-8 -t ascii//TRANSLIT "$INPUT" > "${INPUT}.ascii" 2>/dev/null && \
        mv "${INPUT}.ascii" "$INPUT"
fi

# Fix 9: Remove completely empty rows
sed -i '' '/^[,]*$/d' "$INPUT" 2>/dev/null
sed -i '/^[,]*$/d' "$INPUT" 2>/dev/null

# Fix 10: Trim trailing commas (vendor-a sometimes adds extra)
sed -i '' 's/,*$//' "$INPUT" 2>/dev/null
sed -i 's/,*$//' "$INPUT" 2>/dev/null

LINES=$(wc -l < "$INPUT")
echo "Fixes applied. File has $LINES lines."
