#!/bin/bash
# ============================================
# Database Helper Functions
# Wraps psql calls with connection management
# Author: jthompson
# NOTE: requires psql client installed
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/logging.sh"

DB_CONF="${SCRIPT_DIR}/../configs/database.conf"

# Parse database.conf for connection params
_get_db_param() {
    local profile="$1"
    local key="$2"
    sed -n "/^\[${profile}\]/,/^\[/p" "$DB_CONF" | grep "^${key}=" | head -1 | cut -d'=' -f2
}

# Build connection string
get_conn_string() {
    local profile="${1:-production}"
    local host=$(_get_db_param "$profile" "host")
    local port=$(_get_db_param "$profile" "port")
    local dbname=$(_get_db_param "$profile" "dbname")
    local user=$(_get_db_param "$profile" "user")
    local password=$(_get_db_param "$profile" "password")
    
    echo "postgresql://${user}:${password}@${host}:${port}/${dbname}"
}

# Run SQL query
run_query() {
    local profile="${1:-production}"
    local query="$2"
    local output_file="$3"
    
    local host=$(_get_db_param "$profile" "host")
    local port=$(_get_db_param "$profile" "port")
    local dbname=$(_get_db_param "$profile" "dbname")
    local user=$(_get_db_param "$profile" "user")
    local password=$(_get_db_param "$profile" "password")
    local timeout=$(_get_db_param "$profile" "connection_timeout")
    
    export PGPASSWORD="$password"
    
    local psql_cmd="psql -h ${host} -p ${port} -U ${user} -d ${dbname} -t -A"
    
    if [[ -n "$output_file" ]]; then
        ${psql_cmd} -c "${query}" > "$output_file" 2>&1
    else
        ${psql_cmd} -c "${query}" 2>&1
    fi
    
    local rc=$?
    unset PGPASSWORD
    
    if [[ $rc -ne 0 ]]; then
        log_error "Query failed (profile=${profile}, rc=${rc}): ${query:0:100}..."
    fi
    
    return $rc
}

# Run SQL from file
run_sql_file() {
    local profile="${1:-production}"
    local sql_file="$2"
    
    if [[ ! -f "$sql_file" ]]; then
        log_error "SQL file not found: $sql_file"
        return 1
    fi
    
    local host=$(_get_db_param "$profile" "host")
    local port=$(_get_db_param "$profile" "port")
    local dbname=$(_get_db_param "$profile" "dbname")
    local user=$(_get_db_param "$profile" "user")
    local password=$(_get_db_param "$profile" "password")
    
    export PGPASSWORD="$password"
    
    psql -h "${host}" -p "${port}" -U "${user}" -d "${dbname}" -f "${sql_file}" 2>&1
    
    local rc=$?
    unset PGPASSWORD
    return $rc
}

# Check database connectivity
check_db_connection() {
    local profile="${1:-production}"
    local max_retries=$(_get_db_param "$profile" "max_retries")
    max_retries=${max_retries:-3}
    
    local attempt=0
    while [[ $attempt -lt $max_retries ]]; do
        run_query "$profile" "SELECT 1;" > /dev/null 2>&1
        if [[ $? -eq 0 ]]; then
            log_info "Database connection OK (profile=${profile})"
            return 0
        fi
        attempt=$((attempt + 1))
        log_warn "DB connection attempt ${attempt}/${max_retries} failed (profile=${profile})"
        sleep 5
    done
    
    log_error "Cannot connect to database (profile=${profile})"
    return 1
}

# Get row count from table
get_table_count() {
    local profile="$1"
    local table="$2"
    run_query "$profile" "SELECT COUNT(*) FROM ${table};"
}
