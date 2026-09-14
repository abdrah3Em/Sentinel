"""Instant notification dispatcher for field engineers.

Dispatches CRITICAL and HIGH advisories to Telegram, Slack, or generic webhooks
so on-call engineers receive immediate mobile notifications without needing
to stare at a desktop HMI 24/7.
"""
from __future__ import annotations

import html
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from .. import config
from ..models import Alert

log = logging.getLogger("sentinel.dispatcher")


LEVEL_MARK = {"CRITICAL": "\U0001f6a8", "HIGH": "\u26a0\ufe0f"}


def _brief(text: str, max_sentences: int = 2, max_chars: int = 260) -> str:
    """First sentence or two of a paragraph — the phone shows the headline, the console the rest.

    Whole sentences only: a second sentence is dropped rather than cut mid-word.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    parts, rest = [], text
    while rest:
        cut = rest.find(". ")
        parts.append(rest if cut == -1 else rest[: cut + 1])
        rest = "" if cut == -1 else rest[cut + 2:]
    out = parts[0]
    for sentence in parts[1:max_sentences]:
        if len(out) + 1 + len(sentence) > max_chars:
            break
        out = f"{out} {sentence}"
    if len(out) > max_chars:                       # a single over-long sentence: cut on a word
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"
    return out


def _sections(alert: Alert) -> list[tuple[str, str]]:
    """(heading, body) pairs; a heading of '' is a bare line."""
    mark = LEVEL_MARK.get(alert.level, "\u2139\ufe0f")
    sections: list[tuple[str, str]] = [
        ("", f"{mark} {alert.level} \u00b7 {alert.rule} \u00b7 {alert.score}/100"),
        ("", alert.summary),
    ]
    if alert.equipment:
        sections.append(("Equipment", alert.equipment))
    if alert.command:
        act = alert.command.get("action", "unknown")
        val = alert.command.get("value")
        cmd = f"{act} = {val}" if val is not None else act
        sections.append(("Command", f"{cmd} from {alert.command.get('source', 'unknown')}"))
    if alert.why:
        sections.append(("Why it matters", _brief(alert.why)))
    if alert.recommendation:
        sections.append(("Do this", _brief(alert.recommendation)))
    sections.append(("", f"Confidence {alert.confidence} \u00b7 Sentinel advises only, human decides."))
    return sections


def format_alert_text(alert: Alert) -> str:
    """Plain-text advisory for webhooks and the log."""
    blocks = []
    for heading, body in _sections(alert):
        blocks.append(f"{heading}\n{body}" if heading else body)
    return "\n\n".join(blocks)


def format_alert_html(alert: Alert) -> str:
    """Telegram (HTML parse mode): bold headings, monospace identifiers, one idea per block."""
    e = html.escape
    blocks = []
    for i, (heading, body) in enumerate(_sections(alert)):
        if i == 0:                                   # status line: level bold, rule/score mono
            mark, _, rest = body.partition(" ")
            level, rule, score = [x.strip() for x in rest.split("\u00b7")]
            blocks.append(f"{mark} <b>{e(level)}</b> \u00b7 <code>{e(rule)}</code> \u00b7 {e(score)}")
        elif i == 1:                                 # summary as the bold headline
            blocks.append(f"<b>{e(body)}</b>")
        elif heading == "Command":
            cmd, _, source = body.partition(" from ")
            blocks.append(f"<b>{heading}</b>\n<code>{e(cmd)}</code> from <code>{e(source)}</code>")
        elif heading:
            blocks.append(f"<b>{heading}</b>\n{e(body)}")
        else:
            blocks.append(f"<i>{e(body)}</i>")
    return "\n\n".join(blocks)


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
            _send_telegram(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, format_alert_html(alert))
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
