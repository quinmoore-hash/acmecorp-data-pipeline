#!/bin/bash
# Simple file-based lock manager to prevent concurrent job runs
# This is janky but works... mostly
# Known issue: stale locks if job gets killed with SIGKILL

LOCK_DIR="/tmp/acmecorp_locks"
mkdir -p "$LOCK_DIR" 2>/dev/null

acquire_lock() {
    local job_name="$1"
    local timeout="${2:-60}"
    local lockfile="${LOCK_DIR}/${job_name}.lock"
    
    local elapsed=0
    while [[ -f "$lockfile" ]]; do
        local lock_pid=$(cat "$lockfile" 2>/dev/null)
        # check if the process holding the lock is still alive
        if [[ -n "$lock_pid" ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
            echo "Removing stale lock for $job_name (pid=$lock_pid)" >&2
            rm -f "$lockfile"
            break
        fi
        
        sleep 5
        elapsed=$((elapsed + 5))
        if [[ $elapsed -ge $timeout ]]; then
            echo "ERROR: Timeout acquiring lock for $job_name" >&2
            return 1
        fi
    done
    
    echo $$ > "$lockfile"
    return 0
}

release_lock() {
    local job_name="$1"
    local lockfile="${LOCK_DIR}/${job_name}.lock"
    rm -f "$lockfile"
}

# cleanup on exit - register with: trap 'cleanup_lock "jobname"' EXIT
cleanup_lock() {
    local job_name="$1"
    release_lock "$job_name"
}
