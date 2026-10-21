"""One message shape for every channel, plus the outbound interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from superaide.db.models import ChannelIdentity, Context


@dataclass
class InboundMessage:
    provider: str  # telegram | slack
    tenant_scope: str  # Slack team of the *sender*; "" for Telegram
    sender_id: str  # immutable platform user ID
    chat_id: str  # where to reply
    chat_kind: str  # dm | group
    text: str
    verified: bool  # webhook signature / secret checked, or authenticated socket
    sender_display: str = ""  # display only, never used to decide anything
    sender_username: str = ""
    message_id: str = ""  # the platform's id for this message, used to drop redeliveries
    thread_id: str = ""  # Slack thread_ts or Telegram topic; "" for the main conversation
    external_sender: bool = False  # e.g. Slack Connect user from another company
    chat_title: str = ""
    # Which of our endpoints received it: the Telegram bot id, or the Slack app ("team/app id").
    # "" for the web app. Links are bound to the endpoint that confirmed them (identity/resolver.py).
    endpoint: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


Sources = list[dict[str, Any]]


class Messenger(Protocol):
    """`text` is Ava's Markdown; each messenger converts it to its channel's format. `sources`
    (owner DMs only) become a short footer so the "[1]" marks point at something.

    Sends may return False when the platform refused that one message, and raise
    channels.health.DeliveryFailed when the channel itself is down (token rejected, unreachable)."""

    def send_dm(self, identity: ChannelIdentity, text: str) -> bool | None: ...

    def send_to_context(
        self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None
    ) -> bool | None: ...


@dataclass
class FakeMessenger:
    """Records outbound messages instead of sending them (tests and dry runs)."""

    sent: list[dict[str, Any]] = field(default_factory=list)

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        self.sent.append({"to_identity": str(identity.id), "stable_key": identity.stable_key, "text": text})

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> None:
        sent: dict[str, Any] = {"to_context": context.external_id, "text": text, "thread_id": thread_id}
        if sources:
            sent["sources"] = sources
        self.sent.append(sent)


class WebMessenger:
    """The in-app chat. Replies are stored as events by the pipeline and the
    browser reads them back, so there is nothing to push."""

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        return None

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> None:
        return None
