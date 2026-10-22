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


@dataclass
class InboundAction:
    """A button pressed on one of our messages (Telegram callback_query; later Slack block_actions).
    Who pressed it is the platform's immutable id, checked against the database like a message."""

    provider: str  # telegram | slack
    sender_id: str  # immutable platform user ID of the presser
    action_id: str  # the press's own id (callback_query.id): the same press twice is one press
    data: str  # what the button carries (callback_data); opaque, never trusted beyond a lookup
    chat_id: str = ""
    message_id: str = ""
    endpoint: str = ""  # which bot received it
    verified: bool = False


Sources = list[dict[str, Any]]
# A button on an outbound message: (label, data). Telegram's callback_data is 1-64 bytes.
Button = tuple[str, str]


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
    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)

    def send_buttons(self, chat_id: str, text: str, buttons: list[Button]) -> str:
        """Like TelegramMessenger.send_buttons: returns "chat_id:message_id" for later edits."""
        ref = f"{chat_id}:{len(self.sent) + 1}"
        self.sent.append({"to_chat": chat_id, "stable_key": chat_id, "text": text, "buttons": list(buttons),
                          "ref": ref})  # fmt: skip
        return ref

    def edit_message(self, ref: str, text: str) -> bool:
        self.edits.append({"ref": ref, "text": text})
        return True

    def answer_callback(self, callback_id: str, text: str = "") -> bool:
        self.answers.append({"callback_id": callback_id, "text": text})
        return True

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
