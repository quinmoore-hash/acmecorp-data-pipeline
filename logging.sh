#!/bin/bash
# ============================================
# Shared Logging Utility
# Author: jthompson
# Created: 2021-03-15
# 
# Provides log_info, log_warn, log_error, log_debug
# Usage: source utils/logging.sh
# ============================================

LOG_DIR="${LOG_DIR:-/var/log/acmecorp/pipeline}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/pipeline_$(date +%Y%m%d).log}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Ensure log directory exists
mkdir -p "$LOG_DIR" 2>/dev/null

_log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local caller="${BASH_SOURCE[2]:-unknown}:${BASH_LINENO[1]:-0}"
    
    echo "[${timestamp}] [${level}] [${caller}] ${message}" >> "$LOG_FILE"
    
    # Also print to stderr for console visibility
    if [[ "$level" == "ERROR" ]] || [[ "$level" == "WARN" ]]; then
        echo "[${timestamp}] [${level}] ${message}" >&2
    elif [[ "$VERBOSE" == "true" ]]; then
        echo "[${timestamp}] [${level}] ${message}" >&2
    fi
}

log_info() {
    _log "INFO" "$@"
}

log_warn() {
    _log "WARN" "$@"
}

log_error() {
    _log "ERROR" "$@"
}

log_debug() {
    if [[ "$LOG_LEVEL" == "DEBUG" ]]; then
        _log "DEBUG" "$@"
    fi
}

# Rotate logs older than retention period
rotate_logs() {
    local retention_days="${LOG_RETENTION_DAYS:-90}"
    log_info "Rotating logs older than ${retention_days} days"
    find "$LOG_DIR" -name "*.log" -mtime +${retention_days} -delete 2>/dev/null
}
