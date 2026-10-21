"""Slack adapter: request signing, event parsing, sending, Socket Mode.

Signing: https://docs.slack.dev/authentication/verifying-requests-from-slack
Socket Mode: https://docs.slack.dev/apis/events-api/using-socket-mode
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from typing import Any

from superaide.channels.base import InboundMessage
from superaide.db.models import ChannelIdentity, Context

log = logging.getLogger(__name__)
MAX_SKEW_SECONDS = 60 * 5


def verify_signature(
    signing_secret: str, timestamp: str | None, body: bytes, signature: str | None, now: float | None = None
) -> bool:
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - ts) > MAX_SKEW_SECONDS:
        return False  # replayed or stale request
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_event(payload: dict[str, Any], *, our_team_id: str, verified: bool) -> InboundMessage | None:
    event = payload.get("event") or {}
    etype = event.get("type")
    if etype not in ("message", "app_mention"):
        return None
    if event.get("bot_id") or event.get("subtype"):
        return None  # bot messages, edits, joins and other subtypes are ignored
    user = event.get("user")
    text = event.get("text")
    if not user or not isinstance(text, str) or not text.strip():
        return None
    # The sender's own team: differs from ours for Slack Connect users.
    user_team = event.get("user_team") or payload.get("team_id") or ""
    external = bool(our_team_id) and user_team != our_team_id
    channel_type = event.get("channel_type", "")
    chat_kind = "dm" if channel_type == "im" else "group"
    if etype == "message" and chat_kind == "group":
        return None  # in channels the agent only reacts when @-mentioned
    return InboundMessage(
        provider="slack",
        tenant_scope=user_team,
        sender_id=user,
        chat_id=event.get("channel", ""),
        chat_kind=chat_kind,
        text=text,
        verified=verified,
        message_id=event.get("ts", ""),
        thread_id=event.get("thread_ts", "") if event.get("thread_ts") != event.get("ts") else "",
        external_sender=external,
        raw=payload,
    )


class SlackMessenger:
    def __init__(self, token: str, client: Any = None) -> None:
        if client is None:
            from slack_sdk import WebClient

            client = WebClient(token=token)
        self.client = client

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        opened = self.client.conversations_open(users=identity.stable_key)
        self.client.chat_postMessage(channel=opened["channel"]["id"], text=text)

    def send_to_context(self, context: Context, text: str, thread_id: str = "") -> None:
        if thread_id:
            self.client.chat_postMessage(channel=context.external_id, text=text, thread_ts=thread_id)
        else:
            self.client.chat_postMessage(channel=context.external_id, text=text)


def run_socket_mode(bot_token: str, app_token: str, our_team_id: str, handle: Callable[[InboundMessage], Any]) -> None:
    """Receive events over a websocket: no public URL, good for a laptop. Blocks."""
    socket_mode_handler(bot_token, app_token, our_team_id, handle).start()


def socket_mode_handler(bot_token: str, app_token: str, our_team_id: str, handle: Callable[[InboundMessage], Any]):
    """A Socket Mode handler: .connect() to run in the background, .close() to stop."""
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=bot_token)

    def _dispatch(body: dict[str, Any]) -> None:
        # Socket Mode connections are authenticated with the app-level token.
        msg = parse_event(body, our_team_id=our_team_id, verified=True)
        if msg is not None:
            handle(msg)

    @app.event("message")
    def on_message(body: dict[str, Any]) -> None:
        _dispatch(body)

    @app.event("app_mention")
    def on_mention(body: dict[str, Any]) -> None:
        _dispatch(body)

    log.info("Slack Socket Mode ready")
    return SocketModeHandler(app, app_token)
