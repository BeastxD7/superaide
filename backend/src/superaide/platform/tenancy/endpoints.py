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

from superaide.platform.audit import audit
from superaide.platform.tenancy.context import InstanceScope, scope_or_default, use_instance
from superaide.platform.tenancy.models import ChannelEndpoint

log = logging.getLogger(__name__)
TELEGRAM = "telegram"
SLACK = "slack"


class EndpointTaken(Exception):
    """This bot already serves another owner's Ava."""


def webhook_secret(endpoint_id: uuid.UUID) -> str:
    """The secret Telegram sends with each update for this endpoint (X-Telegram-Bot-Api-Secret-Token).
    Derived from the vault's master key, so nothing secret is stored and a stolen database alone
    can't forge it."""
    from superaide.identity.vault.crypto import master_key

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


def check_free(session: Session, key: str, provider: str = TELEGRAM) -> None:
    """Raise EndpointTaken if this bot (or Slack app) serves another instance."""
    found = owner_of(session, provider, key)
    if found is not None and found.agent_id != scope_or_default().agent_id:
        raise EndpointTaken(key)


def register(session: Session, provider: str, key: str, kind: str) -> ChannelEndpoint:
    """This bot or app now serves the instance in scope (replacing any other of that provider it had)."""
    scope = scope_or_default()
    check_free(session, key, provider)
    session.execute(
        delete(ChannelEndpoint).where(
            ChannelEndpoint.agent_id == scope.agent_id,
            ChannelEndpoint.provider == provider,
            ChannelEndpoint.external_key != key,
        )
    )
    row = owner_of(session, provider, key)
    if row is None:
        row = ChannelEndpoint(
            tenant_id=scope.tenant_id, agent_id=scope.agent_id, provider=provider, kind=kind, external_key=key
        )
        session.add(row)
        session.flush()
        audit(session, "channels", "endpoint_registered", provider=provider, endpoint_id=row.id, key=key)
    return row


def register_telegram(session: Session, bot: str) -> ChannelEndpoint:
    return register(session, TELEGRAM, bot, "own_bot")


def remove(session: Session, provider: str) -> None:
    session.execute(
        delete(ChannelEndpoint).where(
            ChannelEndpoint.agent_id == scope_or_default().agent_id, ChannelEndpoint.provider == provider
        )
    )


def remove_telegram(session: Session) -> None:
    remove(session, TELEGRAM)


def slack_key(team_id: str, app_token: str) -> str:
    """The same "<team>/<app>" key the identity layer binds Slack links to (channels/slack.py
    endpoint_of). The app id is part of the app-level token: xapp-1-<APP_ID>-<n>-<secret>."""
    parts = app_token.split("-")
    app_id = parts[2] if len(parts) > 3 and parts[0] == "xapp" else ""
    return f"{team_id}/{app_id}" if app_id else team_id


def register_slack(session: Session, team_id: str, app_token: str) -> ChannelEndpoint:
    return register(session, SLACK, slack_key(team_id, app_token), "own_app")


def scope_of(endpoint: ChannelEndpoint) -> InstanceScope:
    return InstanceScope(tenant_id=endpoint.tenant_id, agent_id=endpoint.agent_id)


def sync_telegram() -> dict[str, InstanceScope]:
    """Every active instance's Telegram bot token -> its instance, registering endpoints as needed.

    A bot saved in two instances (possible before endpoints existed) keeps the first; the
    other instance's copy is ignored and logged until its owner connects another bot.
    """
    from superaide.identity.vault.store import runtime_settings
    from superaide.platform.db.session import session_scope
    from superaide.platform.tenancy.context import default_scope
    from superaide.platform.tenancy.service import active_scopes

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


def sync_slack() -> dict[str, tuple[InstanceScope, str, str]]:
    """Every active instance's own Slack app: app token -> (instance, bot token, team id),
    registering endpoints as needed. An app saved in two instances keeps the first."""
    from superaide.identity.vault.store import runtime_settings
    from superaide.platform.db.session import session_scope
    from superaide.platform.tenancy.context import default_scope
    from superaide.platform.tenancy.service import active_scopes

    with session_scope() as s:
        scopes = active_scopes(s) or [default_scope()]
    apps: dict[str, tuple[InstanceScope, str, str]] = {}
    for scope in scopes:
        with use_instance(scope):
            try:
                settings = runtime_settings()
            except Exception:
                log.exception("couldn't read the Slack tokens of instance %s", scope.agent_id)
                continue
            bot, app, team = settings.slack_bot_token, settings.slack_app_token, settings.slack_team_id
            if not (bot and app):
                continue
            try:
                with session_scope() as s:
                    register_slack(s, team, app)
            except EndpointTaken:
                log.warning("a Slack app already serves another instance; not started for %s", scope.agent_id)
                continue
            apps[app] = (scope, bot, team)
    return apps
