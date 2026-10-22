"""Channel endpoints: which hired Ava a bot belongs to (ADR 0009 T4).

Each owner brings their own Telegram bot (decided 2026-10-22). Saving its token in
Connections registers an endpoint: (telegram, <bot id>) -> that owner's instance. A bot
can serve one Ava only. Inbound messages are routed by their endpoint before anything
else happens: a poller runs inside its bot's instance, and a webhook names its endpoint
in the URL and proves it with that endpoint's own secret.

The identity layer already records which bot confirmed each link (`endpoint_key`, the
bot id); routing by the same id keeps the two in step.

Endpoints are kept in step with the vaults at startup (`sync_telegram`), so existing
installs get theirs without a migration needing the vault's key.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.tenancy.context import InstanceScope, scope_or_default, use_instance
from superaide.tenancy.models import ChannelEndpoint

log = logging.getLogger(__name__)
TELEGRAM = "telegram"


class EndpointTaken(Exception):
    """This bot already serves another owner's Ava."""


def webhook_secret(endpoint_id: uuid.UUID) -> str:
    """The secret Telegram sends with each update for this endpoint (X-Telegram-Bot-Api-Secret-Token).
    Derived from the vault's master key, so nothing secret is stored and a stolen database alone
    can't forge it."""
    from superaide.vault.crypto import master_key

    key, _ = master_key()
    return hmac.new(key, f"telegram-webhook:{endpoint_id}".encode(), hashlib.sha256).hexdigest()


def secret_ok(endpoint_id: uuid.UUID, received: str | None) -> bool:
    return bool(received) and hmac.compare_digest(webhook_secret(endpoint_id), received or "")


def owner_of(session: Session, provider: str, external_key: str) -> ChannelEndpoint | None:
    return session.scalar(
        select(ChannelEndpoint).where(
            ChannelEndpoint.provider == provider,
            ChannelEndpoint.external_key == external_key,
            ChannelEndpoint.status == "active",
        )
    )


def check_free(session: Session, bot: str) -> None:
    """Raise EndpointTaken if this Telegram bot serves another instance."""
    found = owner_of(session, TELEGRAM, bot)
    if found is not None and found.agent_id != scope_or_default().agent_id:
        raise EndpointTaken(bot)


def register_telegram(session: Session, bot: str) -> ChannelEndpoint:
    """This bot now serves the instance in scope (replacing any other bot it had)."""
    scope = scope_or_default()
    check_free(session, bot)
    session.execute(
        delete(ChannelEndpoint).where(
            ChannelEndpoint.agent_id == scope.agent_id,
            ChannelEndpoint.provider == TELEGRAM,
            ChannelEndpoint.external_key != bot,
        )
    )
    row = owner_of(session, TELEGRAM, bot)
    if row is None:
        row = ChannelEndpoint(
            tenant_id=scope.tenant_id, agent_id=scope.agent_id, provider=TELEGRAM, kind="own_bot", external_key=bot
        )
        session.add(row)
        session.flush()
        audit(session, "channels", "endpoint_registered", provider=TELEGRAM, endpoint_id=row.id, bot=bot)
    return row


def remove_telegram(session: Session) -> None:
    session.execute(
        delete(ChannelEndpoint).where(
            ChannelEndpoint.agent_id == scope_or_default().agent_id, ChannelEndpoint.provider == TELEGRAM
        )
    )


def scope_of(endpoint: ChannelEndpoint) -> InstanceScope:
    return InstanceScope(tenant_id=endpoint.tenant_id, agent_id=endpoint.agent_id)


def sync_telegram() -> dict[str, InstanceScope]:
    """Every active instance's Telegram bot token -> its instance, registering endpoints as needed.

    A bot saved in two instances (possible before endpoints existed) keeps the first; the
    other instance's copy is ignored and logged until its owner connects another bot.
    """
    from superaide.db.session import session_scope
    from superaide.tenancy.context import default_scope
    from superaide.tenancy.service import active_scopes
    from superaide.vault.store import runtime_settings

    with session_scope() as s:
        scopes = active_scopes(s) or [default_scope()]  # before anyone hires: the server's own bot, if any
    bots: dict[str, InstanceScope] = {}
    for scope in scopes:
        with use_instance(scope):
            try:
                token = runtime_settings().telegram_bot_token
            except Exception:
                log.exception("couldn't read the Telegram token of instance %s", scope.agent_id)
                continue
            if not token or ":" not in token:
                continue
            bot = token.split(":", 1)[0]
            try:
                with session_scope() as s:
                    register_telegram(s, bot)
            except EndpointTaken:
                log.warning("bot %s already serves another instance; not started for %s", bot, scope.agent_id)
                continue
            bots[token] = scope
    return bots
