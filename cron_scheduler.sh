#!/bin/bash
# ============================================
# Cron Job Installer / Manager
# Installs and manages all pipeline cron jobs
# Author: jthompson
# 
# Usage:
#   ./cron_scheduler.sh install   - Install all cron jobs
#   ./cron_scheduler.sh remove    - Remove all pipeline cron jobs
#   ./cron_scheduler.sh status    - Show current pipeline cron entries
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
CRON_TAG="# ACMECORP_PIPELINE"

ACTION="${1:-status}"

install_crons() {
    echo "Installing pipeline cron jobs..."
    
    # Remove existing pipeline crons first
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    
    # Build new crontab
    (crontab -l 2>/dev/null; cat <<EOF

# ==========================================
# AcmeCorp Data Pipeline Scheduled Jobs
# Installed: $(date)
# DO NOT EDIT MANUALLY - use cron_scheduler.sh
# ==========================================

# Nightly ETL pipeline - 2:00 AM EST
0 2 * * * ${SCRIPT_DIR}/etl_master.sh >> /var/log/acmecorp/pipeline/etl_master.log 2>&1 ${CRON_TAG}

# Hourly incremental sync
0 * * * * ${SCRIPT_DIR}/incremental_sync.sh >> /var/log/acmecorp/pipeline/incremental_sync.log 2>&1 ${CRON_TAG}

# Log parser and alerting - every 15 min
*/15 * * * * ${SCRIPT_DIR}/log_monitor.sh >> /var/log/acmecorp/pipeline/log_monitor.log 2>&1 ${CRON_TAG}

# Database backup - 1:00 AM EST daily
0 1 * * * ${SCRIPT_DIR}/db_backup.sh >> /var/log/acmecorp/pipeline/db_backup.log 2>&1 ${CRON_TAG}

# Weekly full backup - Sunday 3:00 AM
0 3 * * 0 ${SCRIPT_DIR}/db_backup.sh --full >> /var/log/acmecorp/pipeline/db_backup.log 2>&1 ${CRON_TAG}

# Disk cleanup - daily at 6:00 AM
0 6 * * * ${SCRIPT_DIR}/cleanup_old_data.sh >> /var/log/acmecorp/pipeline/cleanup.log 2>&1 ${CRON_TAG}

# Health check - every 5 minutes
*/5 * * * * ${SCRIPT_DIR}/health_check.sh >> /var/log/acmecorp/pipeline/health_check.log 2>&1 ${CRON_TAG}

# Monthly report generation - 1st of month at 8:00 AM
0 8 1 * * ${SCRIPT_DIR}/generate_report.sh >> /var/log/acmecorp/pipeline/reports.log 2>&1 ${CRON_TAG}
EOF
) | crontab -
    
    echo "Cron jobs installed successfully."
    echo "Run '$0 status' to verify."
}

remove_crons() {
    echo "Removing pipeline cron jobs..."
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    echo "Pipeline cron jobs removed."
}

show_status() {
    echo "Current pipeline cron entries:"
    echo "================================"
    crontab -l 2>/dev/null | grep "$CRON_TAG"
    local count=$(crontab -l 2>/dev/null | grep -c "$CRON_TAG")
    echo "================================"
    echo "Total: ${count} jobs"
}

case "$ACTION" in
    install) install_crons ;;
    remove)  remove_crons ;;
    status)  show_status ;;
    *)
        echo "Usage: $0 {install|remove|status}"
        exit 1
        ;;
esac
