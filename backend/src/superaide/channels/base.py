"""One message shape for every channel, plus the outbound interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from superaide.identity.models import ChannelIdentity
from superaide.memory.models import Context


class FileFetchError(Exception):
    """A file could not be fetched from the platform. The message is fixed text written by us (never the platform's
    own words), safe to say to the sender."""


class FileTooBig(FileFetchError):
    """The platform says the file is over the limit (or it turned out to be while downloading)."""


@dataclass(frozen=True)
class FileRef:
    """Which platform file a message carries: its opaque id and the size the platform reports. Never a name, URL or
    MIME type from the client (#511)."""

    id: str
    size: int = 0
    # What the platform's own structure says it is ("photo", "voice", "audio", "document"; "" when it does not say).
    # Only used to skip a download we would not read; what a file is comes from its first bytes (attachments.py).
    kind: str = ""


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
    # A room message that does not mention Ava (#509): stored only if the owner turned reading on for that channel.
    ambient: bool = False
    # Which of our endpoints received it: the Telegram bot id, or the Slack app ("team/app id").
    # "" for the web app. Links are bound to the endpoint that confirmed them (identity/resolver.py).
    endpoint: str = ""
    # Kinds of file the message carries ("photo", "document", "file"...), from the platform's own structure and never
    # from a client-supplied name or MIME type. Nothing is downloaded or read (#511): the pipeline tells the sender.
    attachments: list[str] = field(default_factory=list)
    # The files of `attachments` that may be read (Slack files, Telegram documents): fetched later, by the messenger,
    # only for a known sender (channels/attachments.py).
    file_refs: list[FileRef] = field(default_factory=list)
    # The app chat uploads the bytes with the request (held in memory for this turn only, never stored).
    uploads: list[bytes] = field(default_factory=list, repr=False)
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
    tenant_scope: str = ""  # Slack: the presser's workspace (a Telegram account has none)


Sources = list[dict[str, Any]]
# A button on an outbound message: (label, data). Telegram's callback_data is 1-64 bytes.
Button = tuple[str, str]


class Messenger(Protocol):
    """`text` is Ava's Markdown; each messenger converts it to its channel's format. `sources`
    (owner DMs only) become a short footer so the "[1]" marks point at something.

    `send_to_context` returns the platform id of the message it posted (a string) when the platform gave one,
    and that id is stored on the outbound event so a thread under the reply finds it (#640).
    Sends may return False when the platform refused that one message, and raise
    channels.health.DeliveryFailed when the channel itself is down (token rejected, unreachable)."""

    def send_dm(self, identity: ChannelIdentity, text: str) -> bool | None: ...

    def send_to_context(
        self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None
    ) -> str | bool | None: ...


class Working(Protocol):
    """A "working on it" signal shown to the person while a reply is prepared (typing, a status or a
    placeholder). `close()` ends it and is safe to call twice; the pipeline always calls it, error or not."""

    def close(self) -> None: ...


@dataclass
class FakeWorking:
    log: list[str]
    key: str
    closed: bool = False

    def close(self) -> None:
        if not self.closed:  # idempotent, like the real ones
            self.closed = True
            self.log.append(f"close {self.key}")


@dataclass
class FakeMessenger:
    """Records outbound messages instead of sending them (tests and dry runs)."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    # "start <chat>[/<thread>]" and "close <chat>[/<thread>]" for every working indicator (typing, status, placeholder).
    working_log: list[str] = field(default_factory=list)
    working_fails: bool = False  # the platform refuses the indicator: start-up must not break the reply

    def working(self, chat_id: str, thread_id: str = "") -> Working | None:
        if self.working_fails:
            raise RuntimeError("platform refused the typing indicator")
        key = f"{chat_id}/{thread_id}" if thread_id else chat_id
        self.working_log.append(f"start {key}")
        return FakeWorking(self.working_log, key)

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

    files: dict[str, bytes] = field(default_factory=dict)  # file id -> bytes, what fetch_file returns
    fetch_log: list[str] = field(default_factory=list)

    def fetch_file(self, ref: FileRef, max_bytes: int) -> bytes:
        self.fetch_log.append(ref.id)
        if ref.id not in self.files:
            raise FileFetchError("I couldn't download that file.")
        if len(self.files[ref.id]) > max_bytes:
            raise FileTooBig("too big")
        return self.files[ref.id]

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        self.sent.append({"to_identity": str(identity.id), "stable_key": identity.stable_key, "text": text})

    def open_dm(self, identity: ChannelIdentity) -> str:
        """The id of the one-to-one chat with this person (Slack: conversations.open). Same shape as the tests' DMs."""
        return f"D_{identity.stable_key}"

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> str:
        sent: dict[str, Any] = {"to_context": context.external_id, "text": text, "thread_id": thread_id}
        if sources:
            sent["sources"] = sources
        self.sent.append(sent)
        return f"fake-{len(self.sent)}"  # a platform id, as Slack's ts or Telegram's message_id


class WebMessenger:
    """The in-app chat. Replies are stored as events by the pipeline and the
    browser reads them back, so there is nothing to push."""

    def send_dm(self, identity: ChannelIdentity, text: str) -> None:
        return None

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> None:
        return None


def bot_id(token: str) -> str:
    """A Telegram bot's public numeric id (the part of the token before the colon; not secret)."""
    return token.split(":", 1)[0] if ":" in token else ""


# What the channel adapters and the modules that use them tell each other (ADR 0015, technique 3), so neither
# imports the other. Each adapter registers itself when imported; `superaide.plugins` imports them at startup.
# provider -> builds that provider's outbound messenger from its bot token
MESSENGER_FACTORIES: dict[str, Callable[[str], Messenger]] = {}
# provider -> handles a button press (bot token, the press) that the provider's listener received
PRESS_HANDLERS: dict[str, Callable[[str, InboundAction], Any]] = {}
