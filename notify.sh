#!/bin/bash
# ============================================
# Notification Helper
# Send alerts via Slack, Email, PagerDuty
# Author: mchen
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/logging.sh"

# Load alerting config
ALERTING_CONF="${SCRIPT_DIR}/../configs/alerting.conf"

_parse_conf() {
    local section="$1"
    local key="$2"
    local file="$3"
    # crude ini parser - known to break if values contain = signs
    sed -n "/^\[${section}\]/,/^\[/p" "$file" | grep "^${key}=" | head -1 | cut -d'=' -f2
}

send_slack() {
    local message="$1"
    local severity="${2:-INFO}"
    local channel=$(_parse_conf "slack" "channel" "$ALERTING_CONF")
    local webhook=$(_parse_conf "slack" "webhook" "$ALERTING_CONF")
    
    if [[ -z "$webhook" ]]; then
        log_warn "Slack webhook not configured, skipping notification"
        return 1
    fi
    
    local color="good"
    [[ "$severity" == "WARNING" ]] && color="warning"
    [[ "$severity" == "CRITICAL" ]] && color="danger"
    
    local mention=""
    if [[ "$severity" == "CRITICAL" ]]; then
        mention=$(_parse_conf "slack" "mention_on_critical" "$ALERTING_CONF")
        message="${mention} ${message}"
    fi
    
    local payload=$(cat <<EOF
{
    "channel": "${channel}",
    "attachments": [{
        "color": "${color}",
        "title": "Pipeline Alert [${severity}]",
        "text": "${message}",
        "footer": "acmecorp-pipeline | $(hostname)",
        "ts": $(date +%s)
    }]
}
EOF
)
    
    curl -s -X POST -H 'Content-type: application/json' \
        --data "${payload}" \
        "${webhook}" > /dev/null 2>&1
    
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        log_error "Failed to send Slack notification (rc=${rc})"
    fi
    return $rc
}

send_email() {
    local subject="$1"
    local body="$2"
    local to="${3:-$(_parse_conf "email" "to" "$ALERTING_CONF")}"
    
    local smtp_host=$(_parse_conf "email" "smtp_host" "$ALERTING_CONF")
    local from=$(_parse_conf "email" "from" "$ALERTING_CONF")
    
    # Using mailx - assumes it's installed
    echo "$body" | mailx -s "$subject" \
        -S smtp="$smtp_host" \
        -S from="$from" \
        "$to" 2>/dev/null
    
    if [[ $? -ne 0 ]]; then
        log_warn "Email send failed, falling back to sendmail"
        echo -e "Subject: ${subject}\nFrom: ${from}\nTo: ${to}\n\n${body}" | sendmail "$to" 2>/dev/null
    fi
}

send_pagerduty() {
    local summary="$1"
    local severity="${2:-warning}"
    local integration_key=$(_parse_conf "pagerduty" "integration_key" "$ALERTING_CONF")
    
    if [[ -z "$integration_key" ]]; then
        log_error "PagerDuty integration key not set"
        return 1
    fi
    
    local payload=$(cat <<EOF
{
    "routing_key": "${integration_key}",
    "event_action": "trigger",
    "payload": {
        "summary": "${summary}",
        "severity": "${severity}",
        "source": "$(hostname):acmecorp-pipeline",
        "component": "data-pipeline",
        "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
    }
}
EOF
)
    
    curl -s -X POST \
        -H 'Content-Type: application/json' \
        -d "${payload}" \
        "https://events.pagerduty.com/v2/enqueue" > /dev/null 2>&1
}

# Convenience function
alert() {
    local message="$1"
    local severity="${2:-INFO}"
    
    log_info "Sending alert [${severity}]: ${message}"
    
    send_slack "$message" "$severity"
    
    if [[ "$severity" == "CRITICAL" ]]; then
        send_email "[CRITICAL] Pipeline Alert" "$message"
        send_pagerduty "$message" "critical"
    elif [[ "$severity" == "WARNING" ]]; then
        send_email "[WARNING] Pipeline Alert" "$message"
    fi
}
