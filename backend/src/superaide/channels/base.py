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
    message_id: str = ""
    external_sender: bool = False  # e.g. Slack Connect user from another company
    chat_title: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class Messenger(Protocol):
    def send_dm(self, identity: ChannelIdentity, text: str) -> None: ...

    def send_to_context(self, context: Context, text: str) -> None: ...


@dataclass
class FakeMessenger:
    """Records outbound messages instead of sending them (tests and dry runs)."""

    sent: list[dict[str, str]] = field(default_factory=list)

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        self.sent.append({"to_identity": str(identity.id), "stable_key": identity.stable_key, "text": text})

    def send_to_context(self, context: Context, text: str) -> None:
        self.sent.append({"to_context": context.external_id, "text": text})


class WebMessenger:
    """The in-app chat. Replies are stored as events by the pipeline and the
    browser reads them back, so there is nothing to push."""

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        return None

    def send_to_context(self, context: Context, text: str) -> None:
        return None
