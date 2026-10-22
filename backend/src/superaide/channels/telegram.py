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

from superaide.channels import format as fmt
from superaide.channels import health
from superaide.channels.base import Button, InboundAction, InboundMessage, Sources
from superaide.db.models import ChannelIdentity, Context

log = logging.getLogger(__name__)
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# Telegram answers 401 for a revoked token and 404 for one that isn't a token at all.
AUTH_STATUSES = (401, 404)
AUTH_BACKOFF = 300.0  # seconds between getUpdates tries once the token is rejected
ALLOWED_UPDATES = '["message", "callback_query"]'  # callback_query: escalation buttons (ADR 0010)


def verify_secret(received: str | None, expected: str) -> bool:
    """True only if a secret is configured and the header matches it exactly."""
    if not expected or received is None:
        return False
    return hmac.compare_digest(received.encode(), expected.encode())


def bot_id(token: str) -> str:
    """The bot's public numeric id (the part of the token before the colon; not secret)."""
    return token.split(":", 1)[0] if ":" in token else ""


def parse_update(update: dict[str, Any], *, verified: bool, bot: str = "") -> InboundMessage | None:
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
        # Telegram numbers messages per chat *per bot*: a new bot starts again at 1, so the
        # bot's id is part of the key, or the first messages to a new bot look like repeats.
        message_id=f"{bot}:{message.get('message_id', '')}" if bot else str(message.get("message_id", "")),
        thread_id=str(message.get("message_thread_id") or "") if message.get("is_topic_message") else "",
        chat_title=chat.get("title", ""),
        endpoint=bot,
        raw=update,
    )


def parse_callback(update: dict[str, Any], *, verified: bool, bot: str = "") -> InboundAction | None:
    """A button press (callback_query) on one of our messages, or None."""
    q = update.get("callback_query")
    if not isinstance(q, dict) or not q.get("id"):
        return None
    sender = q.get("from") or {}
    if not sender.get("id") or sender.get("is_bot"):
        return None
    message = q.get("message") or {}
    return InboundAction(
        provider="telegram",
        sender_id=str(sender["id"]),
        action_id=str(q["id"]),
        data=str(q.get("data") or "")[:64],
        chat_id=str((message.get("chat") or {}).get("id", "")),
        message_id=str(message.get("message_id", "")),
        endpoint=bot,
        verified=verified,
    )


def _handle_press(token: str, action: InboundAction) -> None:
    """Escalation buttons (ADR 0010): handled by triage, in the instance this poller runs in."""
    from superaide.triage.buttons import on_telegram_press

    on_telegram_press(token, action)


class TelegramMessenger:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = client or httpx.Client(timeout=15)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return self.http.post(f"{self.base}/sendMessage", json=body)
        except httpx.HTTPError as exc:  # the message would hold the URL, and so the token: log the type only
            log.warning("telegram send failed: %s", type(exc).__name__)
            health.record("telegram", health.UNREACHABLE, f"Couldn't reach Telegram ({type(exc).__name__})")
            raise health.DeliveryFailed("telegram", health.UNREACHABLE, "Telegram couldn't be reached") from None

    def _send(self, chat_id: str, text: str, thread_id: str = "", sources: Sources | None = None) -> bool:
        """Send Markdown as Telegram HTML, split under the 4096-character limit. If Telegram
        can't parse the markup, that message goes again as plain text.

        True when Telegram took it. Raises DeliveryFailed when the token is rejected or Telegram
        can't be reached (and records the channel's health); False for a per-chat refusal, e.g. the
        person blocked the bot (403), which says nothing about the token."""
        for html_text, markdown in fmt.outgoing(
            text, sources, render=fmt.to_telegram_html, footer=fmt.telegram_footer, limit=fmt.TELEGRAM_LIMIT
        ):
            body: dict[str, Any] = {
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if thread_id.isdigit():  # a forum topic
                body["message_thread_id"] = int(thread_id)
            r = self._post(body)
            if r.status_code == 400 and "can't parse entities" in r.text:
                log.warning("telegram refused the formatting, sending plain text: %s", r.text[:200])
                del body["parse_mode"]
                body["text"] = fmt.to_plain(markdown)[: fmt.TELEGRAM_LIMIT]
                r = self._post(body)
            if r.status_code != 200:
                log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
                if r.status_code in AUTH_STATUSES:
                    health.record("telegram", health.AUTH_FAILED, f"Telegram rejected the bot token ({r.status_code})")
                    raise health.DeliveryFailed("telegram", health.AUTH_FAILED, "the bot token was rejected")
                if r.status_code >= 500:
                    health.record("telegram", health.UNREACHABLE, f"Telegram answered {r.status_code}")
                    raise health.DeliveryFailed("telegram", health.UNREACHABLE, f"Telegram answered {r.status_code}")
                return False
        health.record("telegram", health.OK)
        return True

    def _call(self, method: str, body: dict[str, Any]) -> httpx.Response | None:
        try:
            return self.http.post(f"{self.base}/{method}", json=body)
        except httpx.HTTPError as exc:  # the message would hold the URL, and so the token
            log.warning("telegram %s failed: %s", method, type(exc).__name__)
            return None

    def send_buttons(self, chat_id: str, text: str, buttons: list[Button]) -> str | None:
        """Plain text with one row of inline buttons. Returns "chat_id:message_id" (for edits) when
        Telegram took it, None when it refused this message; raises DeliveryFailed when the channel is down."""
        body = {
            "chat_id": chat_id,
            "text": fmt.to_plain(text)[: fmt.TELEGRAM_LIMIT],
            "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]},
        }
        r = self._post(body)
        if r.status_code != 200:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
            if r.status_code in AUTH_STATUSES:
                health.record("telegram", health.AUTH_FAILED, f"Telegram rejected the bot token ({r.status_code})")
                raise health.DeliveryFailed("telegram", health.AUTH_FAILED, "the bot token was rejected")
            if r.status_code >= 500:
                health.record("telegram", health.UNREACHABLE, f"Telegram answered {r.status_code}")
                raise health.DeliveryFailed("telegram", health.UNREACHABLE, f"Telegram answered {r.status_code}")
            return None
        health.record("telegram", health.OK)
        try:
            message_id = r.json()["result"]["message_id"]
        except ValueError, KeyError, TypeError:
            return f"{chat_id}:"
        return f"{chat_id}:{message_id}"

    def edit_message(self, ref: str, text: str) -> bool:
        """Replace a sent message's text and drop its buttons (no reply_markup), so they can't be pressed again."""
        chat_id, _, message_id = ref.partition(":")
        if not chat_id or not message_id.isdigit():
            return False
        body = {"chat_id": chat_id, "message_id": int(message_id), "text": fmt.to_plain(text)[: fmt.TELEGRAM_LIMIT]}
        r = self._call("editMessageText", body)
        return r is not None and r.status_code == 200

    def answer_callback(self, callback_id: str, text: str = "") -> bool:
        """Stop the client's spinner. Telegram expects this for every press, refused ones too."""
        r = self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
        return r is not None and r.status_code == 200

    def send_dm(self, identity: ChannelIdentity, text: str) -> bool:
        # In a private chat the chat ID equals the user ID. The user must have
        # started the bot once, or Telegram refuses the message.
        return self._send(identity.stable_key, text)

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> bool:
        return self._send(context.external_id, text, thread_id, sources)


def set_webhook(token: str, url: str, secret: str, client: httpx.Client | None = None) -> bool:
    """Ask Telegram to deliver this bot's updates to `url`, with `secret` in SECRET_HEADER."""
    http = client or httpx.Client(timeout=15)
    try:
        r = http.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={
                "url": url,
                "secret_token": secret,
                "allowed_updates": ["message", "callback_query"],  # callback_query: escalation buttons
                "drop_pending_updates": False,
            },
        )
        ok = r.status_code == 200 and bool(r.json().get("ok"))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("telegram setWebhook failed: %s", type(exc).__name__)  # str(exc) can hold the token
        return False
    if not ok:
        log.warning("telegram setWebhook refused (%s)", r.status_code)
    return ok


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
        log.warning("telegram deleteWebhook failed: %s", type(exc).__name__)  # str(exc) can hold the token
    offset = 0
    backoff = 3.0
    known = ""  # the health this poller last recorded, so a healthy loop doesn't write every poll
    log.info("Telegram polling started")
    while not stop.is_set():
        try:
            # Named explicitly: a setWebhook's allowed_updates would otherwise carry over to polling.
            params: dict[str, str | int] = {"timeout": 20, "offset": offset, "allowed_updates": ALLOWED_UPDATES}
            r = http.get(f"{base}/getUpdates", params=params)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram poll error: %s", type(exc).__name__)  # str(exc) can hold the URL and token
            stop.wait(3)
            continue
        if r.status_code in AUTH_STATUSES:
            # The token was revoked or regenerated. Retrying every few seconds can't fix that; a new
            # token saved in the app restarts this listener at once (ChannelRunner sets `stop`).
            log.warning("telegram getUpdates refused (%s): %s", r.status_code, data.get("description", ""))
            if known != health.AUTH_FAILED:
                health.record("telegram", health.AUTH_FAILED, f"Telegram rejected the bot token ({r.status_code})")
                known = health.AUTH_FAILED
            stop.wait(AUTH_BACKOFF)
            continue
        if r.status_code != 200 or not data.get("ok", True):
            # 409: another poller or a webhook is active. Telegram answers at once, so wait
            # (longer each time) instead of hammering it.
            log.warning("telegram getUpdates refused (%s): %s", r.status_code, data.get("description", ""))
            stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        backoff = 3.0
        if known != health.OK:
            health.record("telegram", health.OK)
            known = health.OK
        for update in data.get("result", []):
            if isinstance(update.get("update_id"), int):
                offset = max(offset, update["update_id"] + 1)
            try:
                msg = parse_update(update, verified=True, bot=bot_id(token))
                if msg is not None:
                    handle(msg)
                elif (action := parse_callback(update, verified=True, bot=bot_id(token))) is not None:
                    _handle_press(token, action)
            except Exception:  # keep polling even if one update is malformed or fails
                log.exception("failed to handle telegram update")
