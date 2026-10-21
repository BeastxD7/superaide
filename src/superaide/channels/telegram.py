"""Telegram adapter: webhook secret check, update parsing, sending, polling.

Bot API reference: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import hmac
import logging
import threading
from collections.abc import Callable
from typing import Any

import httpx

from superaide.channels.base import InboundMessage
from superaide.db.models import ChannelIdentity, Context

log = logging.getLogger(__name__)
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def verify_secret(received: str | None, expected: str) -> bool:
    """True only if a secret is configured and the header matches it exactly."""
    if not expected or received is None:
        return False
    return hmac.compare_digest(received.encode(), expected.encode())


def parse_update(update: dict[str, Any], *, verified: bool) -> InboundMessage | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from") or {}
    if not sender or sender.get("is_bot"):
        return None  # channel posts and bots are ignored
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = message.get("chat") or {}
    first, last = sender.get("first_name", ""), sender.get("last_name", "")
    return InboundMessage(
        provider="telegram",
        tenant_scope="",
        sender_id=str(sender["id"]),  # numeric and stable; the username can change
        chat_id=str(chat.get("id")),
        chat_kind="dm" if chat.get("type") == "private" else "group",
        text=text,
        verified=verified,
        sender_display=f"{first} {last}".strip(),
        sender_username=sender.get("username", ""),
        message_id=str(message.get("message_id", "")),
        chat_title=chat.get("title", ""),
        raw=update,
    )


class TelegramMessenger:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = client or httpx.Client(timeout=15)

    def _send(self, chat_id: str, text: str) -> None:
        r = self.http.post(f"{self.base}/sendMessage", json={"chat_id": chat_id, "text": text})
        if r.status_code != 200:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        # In a private chat the chat ID equals the user ID. The user must have
        # started the bot once, or Telegram refuses the message.
        self._send(identity.stable_key, text)

    def send_to_context(self, context: Context, text: str) -> None:
        self._send(context.external_id, text)


def poll_forever(token: str, handle: Callable[[InboundMessage], Any], stop: threading.Event | None = None) -> None:
    """Long-polling for local development (no public URL needed).

    Updates come straight from Telegram over TLS with our bot token, so they
    count as verified. Returns when `stop` is set (within one poll, ~20 s).
    """
    stop = stop or threading.Event()
    http = httpx.Client(timeout=30)
    base = f"https://api.telegram.org/bot{token}"
    try:
        http.post(f"{base}/deleteWebhook")  # polling and webhooks are mutually exclusive
    except httpx.HTTPError as exc:
        log.warning("telegram deleteWebhook failed: %s", exc)
    offset = 0
    log.info("Telegram polling started")
    while not stop.is_set():
        try:
            r = http.get(f"{base}/getUpdates", params={"timeout": 20, "offset": offset})
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram poll error: %s", exc)
            stop.wait(3)
            continue
        for update in data.get("result", []):
            offset = max(offset, update["update_id"] + 1)
            msg = parse_update(update, verified=True)
            if msg is not None:
                try:
                    handle(msg)
                except Exception:  # keep polling even if one message fails
                    log.exception("failed to handle telegram update")
