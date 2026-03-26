#!/bin/bash
# File manipulation utilities
# Used across multiple pipeline scripts
# TODO: this has gotten unwieldy, should be refactored

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/logging.sh"

# Check if file exists and is not empty
check_file() {
    local filepath="$1"
    if [[ ! -f "$filepath" ]]; then
        log_error "File not found: $filepath"
        return 1
    fi
    if [[ ! -s "$filepath" ]]; then
        log_warn "File is empty: $filepath"
        return 2
    fi
    return 0
}

# Count lines in a file (excluding header)
count_data_rows() {
    local filepath="$1"
    local has_header="${2:-true}"
    
    if [[ "$has_header" == "true" ]]; then
        echo $(( $(wc -l < "$filepath") - 1 ))
    else
        wc -l < "$filepath"
    fi
}

# Create dated archive copy
archive_file() {
    local filepath="$1"
    local archive_dir="${2:-${DATA_ARCHIVE_DIR:-/opt/acmecorp/data/archive}}"
    local datestamp=$(date +%Y%m%d_%H%M%S)
    local filename=$(basename "$filepath")
    local archive_path="${archive_dir}/${filename%.*}_${datestamp}.${filename##*.}"
    
    mkdir -p "$archive_dir"
    cp "$filepath" "$archive_path"
    
    if [[ $? -eq 0 ]]; then
        log_info "Archived: $filepath -> $archive_path"
        echo "$archive_path"
    else
        log_error "Failed to archive: $filepath"
        return 1
    fi
}

# Move file with retry
move_file() {
    local src="$1"
    local dest="$2"
    local retries="${3:-3}"
    
    local attempt=0
    while [[ $attempt -lt $retries ]]; do
        mv "$src" "$dest" 2>/dev/null
        if [[ $? -eq 0 ]]; then
            log_info "Moved: $src -> $dest"
            return 0
        fi
        attempt=$((attempt + 1))
        log_warn "Move failed (attempt ${attempt}/${retries}): $src -> $dest"
        sleep 2
    done
    
    log_error "Failed to move file after ${retries} attempts: $src"
    return 1
}

# Validate CSV structure
validate_csv() {
    local filepath="$1"
    local expected_cols="$2"
    local delimiter="${3:-,}"
    
    check_file "$filepath" || return 1
    
    local header_cols=$(head -1 "$filepath" | awk -F"$delimiter" '{print NF}')
    
    if [[ -n "$expected_cols" ]] && [[ "$header_cols" -ne "$expected_cols" ]]; then
        log_error "CSV column mismatch: expected ${expected_cols}, got ${header_cols} in $filepath"
        return 1
    fi
    
    # Check for consistent column count (sample first 100 rows)
    local inconsistent=$(head -100 "$filepath" | awk -F"$delimiter" -v cols="$header_cols" 'NF != cols {print NR}')
    if [[ -n "$inconsistent" ]]; then
        log_warn "Inconsistent column counts at lines: $inconsistent"
        return 2
    fi
    
    log_info "CSV validation passed: $filepath (${header_cols} columns)"
    return 0
}

# Validate JSON file
validate_json() {
    local filepath="$1"
    check_file "$filepath" || return 1
    
    # Try python first, fall back to jq
    if command -v python3 &>/dev/null; then
        python3 -m json.tool "$filepath" > /dev/null 2>&1
    elif command -v jq &>/dev/null; then
        jq '.' "$filepath" > /dev/null 2>&1
    else
        log_warn "No JSON validator available (need python3 or jq)"
        return 0
    fi
    
    if [[ $? -ne 0 ]]; then
        log_error "Invalid JSON: $filepath"
        return 1
    fi
    
    log_info "JSON validation passed: $filepath"
    return 0
}

# Get file size in human readable format
file_size_hr() {
    local filepath="$1"
    if [[ -f "$filepath" ]]; then
        ls -lh "$filepath" | awk '{print $5}'
    else
        echo "0"
    fi
}

# Wait for file to appear (used for file-based triggers)
wait_for_file() {
    local filepath="$1"
    local timeout="${2:-300}"  # default 5 min
    local interval="${3:-10}"
    
    local elapsed=0
    log_info "Waiting for file: $filepath (timeout: ${timeout}s)"
    
    while [[ ! -f "$filepath" ]]; do
        sleep $interval
        elapsed=$((elapsed + interval))
        if [[ $elapsed -ge $timeout ]]; then
            log_error "Timeout waiting for file: $filepath"
            return 1
        fi
    done
    
    # wait for file to finish writing (size stabilizes)
    local prev_size=0
    local curr_size=1
    while [[ "$prev_size" != "$curr_size" ]]; do
        prev_size=$(stat -f%z "$filepath" 2>/dev/null || stat -c%s "$filepath" 2>/dev/null)
        sleep 2
        curr_size=$(stat -f%z "$filepath" 2>/dev/null || stat -c%s "$filepath" 2>/dev/null)
    done
    
    log_info "File ready: $filepath ($(file_size_hr "$filepath"))"
    return 0
}
