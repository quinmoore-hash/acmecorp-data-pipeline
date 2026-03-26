"""Tests for acmecorp_pipeline.notifications module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from acmecorp_pipeline.config import EmailConfig, PagerDutyConfig, SlackConfig
from acmecorp_pipeline.notifications import send_email, send_pagerduty, send_slack


@pytest.fixture
def slack_config() -> SlackConfig:
    return SlackConfig(webhook_url="https://hooks.slack.com/test", channel="#test")


@pytest.fixture
def email_config() -> EmailConfig:
    return EmailConfig(smtp_host="localhost", smtp_port=25, from_addr="test@test.com", to_addr="to@test.com")


@pytest.fixture
def pagerduty_config() -> PagerDutyConfig:
    return PagerDutyConfig(integration_key="test-key")


class TestSendSlack:
    @patch("acmecorp_pipeline.notifications.requests.post")
    def test_sends_post_request(self, mock_post: MagicMock, slack_config: SlackConfig) -> None:
        mock_post.return_value.status_code = 200
        send_slack("Test message", "INFO", slack_config)
        mock_post.assert_called_once()

    @patch("acmecorp_pipeline.notifications.requests.post")
    def test_severity_colors(self, mock_post: MagicMock, slack_config: SlackConfig) -> None:
        mock_post.return_value.status_code = 200

        for severity in ("INFO", "WARNING", "CRITICAL"):
            send_slack("msg", severity, slack_config)

        assert mock_post.call_count == 3

    def test_empty_webhook_skips(self) -> None:
        cfg = SlackConfig(webhook_url="", channel="#test")
        # Should not raise
        send_slack("msg", "INFO", cfg)


class TestSendEmail:
    @patch("acmecorp_pipeline.notifications.smtplib.SMTP")
    def test_sends_email(self, mock_smtp_class: MagicMock, email_config: EmailConfig) -> None:
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        send_email("Subject", "Body", email_config)
        mock_smtp.sendmail.assert_called_once()


class TestSendPagerduty:
    @patch("acmecorp_pipeline.notifications.requests.post")
    def test_sends_trigger(self, mock_post: MagicMock, pagerduty_config: PagerDutyConfig) -> None:
        mock_post.return_value.status_code = 202
        send_pagerduty("Test alert", "critical", pagerduty_config)
        mock_post.assert_called_once()

    def test_empty_key_skips(self) -> None:
        cfg = PagerDutyConfig(integration_key="")
        # Should not raise
        send_pagerduty("msg", "critical", cfg)
