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
from contextvars import ContextVar
from typing import Any

from superaide.channels import format as fmt
from superaide.channels import health
from superaide.channels.base import InboundMessage, Sources
from superaide.db.models import ChannelIdentity, Context

log = logging.getLogger(__name__)
MAX_SKEW_SECONDS = 60 * 5
# Slack's error codes for a token that no longer works.
AUTH_ERRORS = frozenset({"invalid_auth", "token_revoked", "token_expired", "account_inactive", "not_authed"})


def auth_error(exc: BaseException) -> str:
    """The Slack error code when `exc` says our token was rejected, else "". Works for SlackApiError
    (and anything shaped like it) without importing slack_sdk here, and for Bolt's start-up error."""
    response = getattr(exc, "response", None)
    code = ""
    if response is not None:
        try:
            code = str(response.get("error") or "")
        except Exception:
            code = ""
    if code in AUTH_ERRORS:
        return code
    text = str(exc)
    return next((c for c in AUTH_ERRORS if c in text), "")


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


def endpoint_of(payload: dict[str, Any]) -> str:
    """Our side of a Slack event: the workspace the app is installed in, and the app."""
    team, app = str(payload.get("team_id") or ""), str(payload.get("api_app_id") or "")
    return f"{team}/{app}" if app else team


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
        endpoint=endpoint_of(payload),
        raw=payload,
    )


THREAD_MESSAGES = 25  # how much of a thread Ava reads when the owner @-mentions her in it
WORKING_TEXT = "Working on it…"
WORKING_STATUS = "is working on it…"
# Slack errors that say the status API will never work for this app (so we stop asking and use the placeholder).
STATUS_UNAVAILABLE = frozenset(
    {"missing_scope", "unknown_method", "method_deprecated", "not_allowed_token_type", "feature_not_enabled"}
)
# The working signal of the turn running in this thread, so the reply that ends it can take over its placeholder.
_ACTIVE: ContextVar[SlackWorking | None] = ContextVar("slack_working", default=None)


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    try:
        return str(response.get("error") or "") if response is not None else ""
    except Exception:
        return ""


class SlackWorking:
    """Either an assistant-thread status (cleared by Slack when the reply is posted) or a "Working on it…"
    placeholder message. The reply is posted as a new message (an edit would not notify) and the placeholder is deleted
    after it. `close()` removes a placeholder that got no reply (the turn failed), and is safe to call twice."""

    def __init__(self, messenger: SlackMessenger, channel: str, thread_id: str, ts: str = "", status: bool = False):
        self.messenger, self.channel, self.thread_id, self.ts, self.status = messenger, channel, thread_id, ts, status
        self.consumed = False
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if _ACTIVE.get() is self:
            _ACTIVE.set(None)
        if self.ts and not self.consumed:
            self.messenger._discard(self.channel, self.ts)
        if self.status and not self.consumed:
            try:  # an empty status clears it; Slack also clears it when a message is posted
                self.messenger.client.assistant_threads_setStatus(
                    channel_id=self.channel, thread_ts=self.thread_id, status=""
                )
            except Exception:
                log.debug("slack: clearing the status failed", exc_info=True)


class SlackMessenger:
    status_unavailable = False  # set once Slack says assistant.threads.setStatus can't be used by this app

    def __init__(self, token: str, client: Any = None) -> None:
        if client is None:
            from slack_sdk import WebClient

            client = WebClient(token=token)
        self.client = client

    def _call(self, method: str, **kwargs: Any) -> Any:
        """One Web API call. A rejected token or an unreachable Slack becomes DeliveryFailed and is
        recorded as the channel's health; other Slack errors (e.g. channel_not_found) propagate."""
        try:
            result = getattr(self.client, method)(**kwargs)
        except Exception as exc:
            code = auth_error(exc)
            if code:
                health.record("slack", health.AUTH_FAILED, f"Slack rejected the token ({code})")
                raise health.DeliveryFailed("slack", health.AUTH_FAILED, "the Slack token was rejected") from None
            if isinstance(exc, OSError):  # connection refused, DNS, timeouts (urllib errors are OSErrors)
                health.record("slack", health.UNREACHABLE, f"Couldn't reach Slack ({type(exc).__name__})")
                raise health.DeliveryFailed("slack", health.UNREACHABLE, "Slack couldn't be reached") from None
            raise
        health.record("slack", health.OK)
        return result

    def working(self, chat_id: str, thread_id: str = "") -> SlackWorking | None:
        """Show that Ava is working: the assistant-thread status where Slack offers it (needs a thread and the
        assistant:write scope), otherwise a "Working on it…" placeholder that the reply later edits. Never raises."""
        handle: SlackWorking | None = None
        try:
            if thread_id and not self.status_unavailable and hasattr(self.client, "assistant_threads_setStatus"):
                try:
                    self.client.assistant_threads_setStatus(
                        channel_id=chat_id, thread_ts=thread_id, status=WORKING_STATUS
                    )
                    handle = SlackWorking(self, chat_id, thread_id, status=True)
                except Exception as exc:
                    if _error_code(exc) in STATUS_UNAVAILABLE:
                        self.status_unavailable = True
            if handle is None:
                kwargs: dict[str, Any] = {"channel": chat_id, "text": WORKING_TEXT}
                if thread_id:
                    kwargs["thread_ts"] = thread_id
                posted = self._call("chat_postMessage", **kwargs)
                handle = SlackWorking(self, chat_id, thread_id, ts=str(posted["ts"]))
        except Exception as exc:  # cosmetic: the reply goes out normally without it
            log.warning("slack: working indicator unavailable (%s)", type(exc).__name__)
            return None
        _ACTIVE.set(handle)
        return handle

    def _discard(self, channel: str, ts: str) -> None:
        try:
            self._call("chat_delete", channel=channel, ts=ts)
        except Exception as exc:
            log.warning("slack: couldn't remove the placeholder (%s)", type(exc).__name__)

    def _post(self, channel: str, text: str, thread_id: str = "", sources: Sources | None = None) -> bool:
        """Markdown goes out as Slack mrkdwn, split under Slack's recommended length."""
        messages = fmt.outgoing(text, sources, render=fmt.to_slack, footer=fmt.slack_footer, limit=fmt.SLACK_LIMIT)
        active = _ACTIVE.get()
        if not (
            active is not None
            and not active.consumed
            and active.messenger is self
            and (active.channel, active.thread_id) == (channel, thread_id)
        ):
            active = None
        pending = active if active is not None and active.ts else None  # a status has no ts
        for mrkdwn, _ in messages:
            if thread_id:
                self._call("chat_postMessage", channel=channel, text=mrkdwn, thread_ts=thread_id)
            else:
                self._call("chat_postMessage", channel=channel, text=mrkdwn)
            if active is not None and not active.consumed:
                active.consumed = True  # a status is cleared by Slack once a message is posted
                if pending is not None:
                    # An edit would not notify anyone, so the reply is a new message; the placeholder goes after it
                    # (best-effort: if the delete fails it stays, the reply is never lost).
                    self._discard(channel, pending.ts)
        return True

    def send_dm(self, identity: ChannelIdentity, text: str) -> bool:
        opened = self._call("conversations_open", users=identity.stable_key)
        return self._post(opened["channel"]["id"], text)

    def thread_history(self, channel: str, thread_ts: str, limit: int = THREAD_MESSAGES) -> list[dict[str, Any]]:
        """The messages of one thread, oldest first: [{"user", "text", "ts", "bot"}]. [] when Slack refuses
        (missing channels:history / groups:history scope, an archived channel): Ava then answers without it."""
        try:
            reply = self._call("conversations_replies", channel=channel, ts=thread_ts, limit=limit)
        except Exception as exc:
            log.info("slack: thread history unavailable (%s)", _error_code(exc) or type(exc).__name__)
            return []
        out: list[dict[str, Any]] = []
        for m in (reply.get("messages") if reply else None) or []:
            if m.get("subtype") in {"channel_join", "channel_leave"} or not str(m.get("text") or "").strip():
                continue
            out.append(
                {"user": str(m.get("user") or ""), "text": str(m["text"]), "ts": str(m.get("ts") or ""),
                 "bot": bool(m.get("bot_id"))}
            )  # fmt: skip
        return out

    def send_to_context(self, context: Context, text: str, thread_id: str = "", sources: Sources | None = None) -> bool:
        return self._post(context.external_id, text, thread_id, sources)


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
