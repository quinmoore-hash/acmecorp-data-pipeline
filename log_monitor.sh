#!/bin/bash
# ============================================
# Log Monitor & Alerting
# Parses pipeline logs for errors and anomalies
# Runs every 15 minutes via cron
# Author: asingh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

source "${BASE_DIR}/utils/logging.sh"
source "${BASE_DIR}/utils/notify.sh"
source "${BASE_DIR}/configs/pipeline.env"

LOG_SCAN_DIR="${LOG_DIR:-/var/log/acmecorp/pipeline}"
STATE_FILE="/tmp/acmecorp_log_monitor_state"
ALERT_COOLDOWN_FILE="/tmp/acmecorp_alert_cooldown"

# Cooldown period in seconds (don't spam alerts)
ALERT_COOLDOWN=900  # 15 min

# Error patterns to watch for
ERROR_PATTERNS=(
    "FATAL"
    "CRITICAL"
    "OOM"
    "out of memory"
    "disk full"
    "connection refused"
    "permission denied"
    "segmentation fault"
    "killed"
    "no space left on device"
)

# Get last scanned position
get_last_position() {
    local logfile="$1"
    grep "^${logfile}:" "$STATE_FILE" 2>/dev/null | cut -d: -f2
}

save_position() {
    local logfile="$1"
    local position="$2"
    
    if grep -q "^${logfile}:" "$STATE_FILE" 2>/dev/null; then
        sed -i '' "s|^${logfile}:.*|${logfile}:${position}|" "$STATE_FILE" 2>/dev/null || \
        sed -i "s|^${logfile}:.*|${logfile}:${position}|" "$STATE_FILE"
    else
        echo "${logfile}:${position}" >> "$STATE_FILE"
    fi
}

check_cooldown() {
    local alert_key="$1"
    if [[ -f "$ALERT_COOLDOWN_FILE" ]]; then
        local last_alert=$(grep "^${alert_key}:" "$ALERT_COOLDOWN_FILE" 2>/dev/null | cut -d: -f2)
        if [[ -n "$last_alert" ]]; then
            local now=$(date +%s)
            local elapsed=$((now - last_alert))
            if [[ $elapsed -lt $ALERT_COOLDOWN ]]; then
                return 1  # still in cooldown
            fi
        fi
    fi
    return 0
}

set_cooldown() {
    local alert_key="$1"
    local now=$(date +%s)
    if grep -q "^${alert_key}:" "$ALERT_COOLDOWN_FILE" 2>/dev/null; then
        sed -i '' "s|^${alert_key}:.*|${alert_key}:${now}|" "$ALERT_COOLDOWN_FILE" 2>/dev/null || \
        sed -i "s|^${alert_key}:.*|${alert_key}:${now}|" "$ALERT_COOLDOWN_FILE"
    else
        echo "${alert_key}:${now}" >> "$ALERT_COOLDOWN_FILE"
    fi
}

scan_log_file() {
    local logfile="$1"
    local last_pos=$(get_last_position "$logfile")
    last_pos=${last_pos:-0}
    
    local current_size=$(wc -c < "$logfile" 2>/dev/null)
    current_size=${current_size:-0}
    
    # If file was rotated (smaller than last position), start from 0
    if [[ $current_size -lt $last_pos ]]; then
        last_pos=0
    fi
    
    if [[ $current_size -le $last_pos ]]; then
        return 0  # nothing new
    fi
    
    # Extract new content
    local new_content=$(tail -c +$((last_pos + 1)) "$logfile")
    
    # Scan for error patterns
    local errors_found=0
    local error_lines=""
    
    for pattern in "${ERROR_PATTERNS[@]}"; do
        local matches=$(echo "$new_content" | grep -i "$pattern" | head -5)
        if [[ -n "$matches" ]]; then
            errors_found=$((errors_found + 1))
            error_lines="${error_lines}\n${matches}"
        fi
    done
    
    # Count ERROR level log entries
    local error_count=$(echo "$new_content" | grep -c "\[ERROR\]")
    local warn_count=$(echo "$new_content" | grep -c "\[WARN\]")
    
    if [[ $errors_found -gt 0 ]] || [[ $error_count -gt 10 ]]; then
        local alert_key="logmon_$(basename "$logfile")"
        if check_cooldown "$alert_key"; then
            alert "Log Monitor: $(basename "$logfile") - ${error_count} errors, ${warn_count} warnings detected.\nSample:${error_lines}" "WARNING"
            set_cooldown "$alert_key"
        fi
    fi
    
    save_position "$logfile" "$current_size"
}

# Main
touch "$STATE_FILE" "$ALERT_COOLDOWN_FILE" 2>/dev/null

log_debug "Scanning logs in ${LOG_SCAN_DIR}..."

for logfile in "${LOG_SCAN_DIR}"/*.log; do
    [[ -f "$logfile" ]] || continue
    scan_log_file "$logfile"
done

# Also check disk usage
DISK_USAGE=$(df -h "$LOG_SCAN_DIR" | tail -1 | awk '{print $5}' | tr -d '%')
if [[ $DISK_USAGE -gt 80 ]]; then
    if check_cooldown "disk_usage"; then
        alert "Disk usage on log volume: ${DISK_USAGE}%" "WARNING"
        set_cooldown "disk_usage"
    fi
fi
if [[ $DISK_USAGE -gt 95 ]]; then
    if check_cooldown "disk_critical"; then
        alert "CRITICAL: Disk usage at ${DISK_USAGE}%! Pipeline may fail!" "CRITICAL"
        set_cooldown "disk_critical"
    fi
fi

log_debug "Log monitor scan complete"
