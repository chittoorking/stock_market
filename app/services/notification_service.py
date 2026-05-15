"""
Notification service — Telegram, Slack, Email, SMS.
Multi-channel notification dispatch for trade alerts and system events.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_telegram(
    message: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "Markdown",
):
    """Send a message via Telegram bot."""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID

    if not token or not target_chat:
        logger.debug("Telegram not configured, skipping notification")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": target_chat,
                    "text": message,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %s", resp.text)
    except Exception as e:
        logger.error("Telegram notification error: %s", e)


async def send_slack(message: str):
    """Send a message to Slack via webhook."""
    webhook = settings.SLACK_WEBHOOK_URL
    if not webhook:
        logger.debug("Slack not configured, skipping notification")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook,
                json={"text": message},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning("Slack send failed: %s", resp.text)
    except Exception as e:
        logger.error("Slack notification error: %s", e)


async def send_email(
    to: str,
    subject: str,
    body: str,
    html: Optional[str] = None,
):
    """Send an email via SMTP."""
    if not settings.EMAIL_USERNAME:
        logger.debug("Email not configured, skipping notification")
        return

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to

        msg.attach(MIMEText(body, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(settings.EMAIL_USERNAME, settings.EMAIL_PASSWORD)
            server.send_message(msg)

        logger.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        logger.error("Email send error: %s", e)


async def send_sms(phone: str, message: str):
    """Send an SMS (placeholder — integrate with Twilio/MSG91)."""
    logger.info("SMS to %s: %s", phone, message[:160])
    # TODO: Integrate with SMS provider


async def notify_all(message: str, level: str = "info"):
    """
    Send notification to all configured channels.
    level: "info", "warning", "critical"
    """
    if level == "critical":
        # Critical alerts go everywhere
        await send_telegram(f"🚨 {message}")
        await send_slack(f"🚨 {message}")
    elif level == "warning":
        await send_telegram(f"⚠️ {message}")
    else:
        await send_telegram(message)
