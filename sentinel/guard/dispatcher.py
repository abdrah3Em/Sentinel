"""Instant notification dispatcher for field engineers.

Dispatches CRITICAL and HIGH advisories to Telegram, Slack, or generic webhooks
so on-call engineers receive immediate mobile notifications without needing
to stare at a desktop HMI 24/7.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from .. import config
from ..models import Alert

log = logging.getLogger("sentinel.dispatcher")


def format_alert_text(alert: Alert) -> str:
    """Format an alert into a clean, punchy mobile advisory."""
    command_str = ""
    if alert.command:
        act = alert.command.get("action", "unknown")
        val = alert.command.get("value")
        command_str = f"{act} = {val}" if val is not None else act

    lines = [
        f"🚨 SENTINEL {alert.level} ADVISORY",
        f"Rule: {alert.rule} (Score: {alert.score}/100)",
        f"Summary: {alert.summary}",
        f"Equipment: {alert.equipment or 'Plant'}",
    ]
    if command_str:
        lines.append(f"Command: {command_str} (from {alert.command.get('source', 'unknown')})")
    if alert.why:
        lines.append(f"Why: {alert.why}")
    if alert.recommendation:
        lines.append(f"Action: {alert.recommendation}")
    lines.append(f"Confidence: {alert.confidence} — Sentinel advises only, human decides.")
    return "\n".join(lines)


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        if resp.status != 200:
            log.warning("Telegram dispatch returned status %s", resp.status)


def _send_webhook(url: str, alert: Alert, text: str) -> None:
    payload = json.dumps({
        "text": text,
        "content": text,  # for Discord
        "alert": alert.to_dict(),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        if resp.status not in (200, 204):
            log.warning("Webhook dispatch returned status %s", resp.status)


def _dispatch_worker(alert: Alert) -> None:
    text = format_alert_text(alert)
    dispatched = False

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            _send_telegram(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, text)
            log.info("Dispatched %s alert to Telegram", alert.level)
            dispatched = True
        except Exception as e:
            log.error("Failed to dispatch to Telegram: %s", e)

    if config.WEBHOOK_URL:
        try:
            _send_webhook(config.WEBHOOK_URL, alert, text)
            log.info("Dispatched %s alert to Webhook", alert.level)
            dispatched = True
        except Exception as e:
            log.error("Failed to dispatch to Webhook: %s", e)

    if not dispatched:
        log.info("[NOTIFICATION DISPATCHER] (Simulated Mobile Alert - configure SENTINEL_WEBHOOK_URL or SENTINEL_TELEGRAM_BOT_TOKEN):\n%s", text)


def dispatch_alert(alert: Alert) -> None:
    """Evaluate if alert meets severity threshold and dispatch asynchronously."""
    min_order = config.SEVERITY_ORDER.get(config.DISPATCH_MIN_LEVEL, 2)
    alert_order = config.SEVERITY_ORDER.get(alert.level, 0)
    if alert_order < min_order:
        return

    threading.Thread(target=_dispatch_worker, args=(alert,), daemon=True).start()
