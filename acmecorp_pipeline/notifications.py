"""Notification helpers: Slack, Email, PagerDuty.

Replaces ``notify.sh``.  Uses ``requests`` for HTTP calls and
``smtplib`` for email instead of ``curl`` / ``mailx``.
"""

from __future__ import annotations

import json
import smtplib
import socket
import time
from email.mime.text import MIMEText
from typing import Optional

import requests

from acmecorp_pipeline.config import (
    EmailConfig,
    PagerDutyConfig,
    PipelineConfig,
    SlackConfig,
)
from acmecorp_pipeline.logging_utils import get_logger

log = get_logger("notifications")


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def send_slack(
    message: str,
    severity: str = "INFO",
    cfg: Optional[SlackConfig] = None,
) -> bool:
    """Post a message to Slack via incoming webhook.

    Returns ``True`` on success.
    """
    if cfg is None or not cfg.enabled or not cfg.webhook_url:
        log.warning("Slack webhook not configured, skipping notification")
        return False

    color_map = {"INFO": "good", "WARNING": "warning", "CRITICAL": "danger"}
    color = color_map.get(severity, "good")

    if severity == "CRITICAL" and cfg.mention_on_critical:
        message = f"{cfg.mention_on_critical} {message}"

    payload = {
        "channel": cfg.channel,
        "attachments": [
            {
                "color": color,
                "title": f"Pipeline Alert [{severity}]",
                "text": message,
                "footer": f"acmecorp-pipeline | {socket.gethostname()}",
                "ts": int(time.time()),
            }
        ],
    }

    try:
        resp = requests.post(
            cfg.webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Failed to send Slack notification: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(
    subject: str,
    body: str,
    cfg: Optional[EmailConfig] = None,
    to: Optional[str] = None,
) -> bool:
    """Send a plain-text email via SMTP.

    Returns ``True`` on success.
    """
    if cfg is None or not cfg.enabled or not cfg.smtp_host:
        log.warning("Email not configured, skipping notification")
        return False

    recipient = to or cfg.to_addr
    if not recipient:
        log.warning("No email recipient configured")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = recipient
    if cfg.cc_addr:
        msg["Cc"] = cfg.cc_addr

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            all_recipients = [r.strip() for r in recipient.split(",")]
            if cfg.cc_addr:
                all_recipients += [r.strip() for r in cfg.cc_addr.split(",")]
            smtp.sendmail(cfg.from_addr, all_recipients, msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Email send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

def send_pagerduty(
    summary: str,
    severity: str = "warning",
    cfg: Optional[PagerDutyConfig] = None,
) -> bool:
    """Trigger a PagerDuty event via Events API v2.

    Returns ``True`` on success.
    """
    if cfg is None or not cfg.enabled or not cfg.integration_key:
        log.error("PagerDuty integration key not set")
        return False

    payload = {
        "routing_key": cfg.integration_key,
        "event_action": "trigger",
        "payload": {
            "summary": summary,
            "severity": severity,
            "source": f"{socket.gethostname()}:acmecorp-pipeline",
            "component": "data-pipeline",
        },
    }

    try:
        resp = requests.post(
            "https://events.pagerduty.com/v2/enqueue",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("PagerDuty notification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def alert(
    message: str,
    severity: str = "INFO",
    config: Optional[PipelineConfig] = None,
) -> None:
    """Route an alert to the appropriate channels based on severity.

    Mirrors the ``alert()`` function in the original ``notify.sh``.
    """
    log.info("Sending alert [%s]: %s", severity, message)

    slack_cfg = config.slack if config else None
    email_cfg = config.email if config else None
    pd_cfg = config.pagerduty if config else None

    send_slack(message, severity, slack_cfg)

    if severity == "CRITICAL":
        send_email("[CRITICAL] Pipeline Alert", message, email_cfg)
        send_pagerduty(message, "critical", pd_cfg)
    elif severity == "WARNING":
        send_email("[WARNING] Pipeline Alert", message, email_cfg)
