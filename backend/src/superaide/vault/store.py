"""Saving, reading and applying the owner's connections.

Credentials saved in the app take precedence over the same setting in .env.
`runtime_settings()` is the Settings with those credentials applied; the agent
and the channel listeners are rebuilt from it whenever a connection changes.

Each hired Ava has its own credentials (ADR 0009 T3): everything here reads and
writes the instance in scope only, and each secret is sealed with its instance in
the associated data. Owners bring their own model keys; on a hosted server
(DEPLOY_MODE=hosted) the .env credentials are never used for an owner, so an owner
with nothing connected sees "connect your AI model", never someone else's key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import Settings, get_settings
from superaide.db.models import IntegrationSecret
from superaide.db.session import session_scope
from superaide.tenancy.context import scope_or_default
from superaide.vault.crypto import decrypt, encrypt, mask

# Which stored credential fills which setting.
FIELDS: dict[tuple[str, str], str] = {
    ("anthropic", "api_key"): "anthropic_api_key",
    ("brave", "api_key"): "brave_search_api_key",
    ("telegram", "bot_token"): "telegram_bot_token",
    ("slack", "bot_token"): "slack_bot_token",
    ("slack", "app_token"): "slack_app_token",
    ("slack", "team_id"): "slack_team_id",
    ("google", "client_id"): "google_client_id",
    ("google", "client_secret"): "google_client_secret",
    ("google", "refresh_token"): "google_refresh_token",
}
SECRET_NAMES = {"api_key", "bot_token", "app_token", "client_secret", "refresh_token"}  # ids aren't secrets


def slot(provider: str, name: str, agent_id: uuid.UUID | None = None) -> str:
    """The associated data a secret is sealed with: bound to its instance, or (legacy) not."""
    return f"{provider}/{name}" if agent_id is None else f"{agent_id}/{provider}/{name}"


def _mine() -> Any:
    return IntegrationSecret.agent_id == scope_or_default().agent_id


def seal(row: IntegrationSecret, value: str) -> None:
    """Encrypt `value` into `row`, bound to the instance in scope."""
    scope = scope_or_default()
    row.agent_id, row.tenant_id, row.aad = scope.agent_id, scope.tenant_id, "instance"
    row.ciphertext = encrypt(value, slot(row.provider, row.name, row.agent_id))


def unseal(row: IntegrationSecret) -> str:
    """The plaintext. A legacy row (sealed before instances) is resealed to its instance on the way."""
    if row.aad == "legacy":
        value = decrypt(row.ciphertext, slot(row.provider, row.name))
        row.ciphertext, row.aad = encrypt(value, slot(row.provider, row.name, row.agent_id)), "instance"
        return value
    return decrypt(row.ciphertext, slot(row.provider, row.name, row.agent_id))


def find(session: Session, provider: str, name: str) -> IntegrationSecret | None:
    return session.scalar(
        select(IntegrationSecret).where(_mine(), IntegrationSecret.provider == provider, IntegrationSecret.name == name)
    )


def save(
    session: Session, provider: str, values: dict[str, str], *, user_id: uuid.UUID | None, meta: dict[str, Any]
) -> None:
    now = datetime.now(UTC)
    for name, value in values.items():
        if (provider, name) not in FIELDS:
            raise ValueError(f"unknown setting {provider}/{name}")
        row = find(session, provider, name)
        if row is None:
            row = IntegrationSecret(provider=provider, name=name, ciphertext="")
            session.add(row)
        seal(row, value)
        row.hint = mask(value) if name in SECRET_NAMES else value[:40]
        row.meta = meta
        row.set_by_user_id = user_id
        row.validated_at = now
        row.updated_at = now
    # Never the value: only which slots changed and who changed them.
    audit(session, "connections", "connected", provider=provider, fields=sorted(values), user_id=user_id)


def remove(session: Session, provider: str, *, user_id: uuid.UUID | None) -> int:
    rows = session.scalars(select(IntegrationSecret).where(_mine(), IntegrationSecret.provider == provider)).all()
    for r in rows:
        session.delete(r)
    audit(session, "connections", "disconnected", provider=provider, user_id=user_id)
    return len(rows)


def remove_field(session: Session, provider: str, name: str) -> None:
    row = find(session, provider, name)
    if row is not None:
        session.delete(row)


def stored(session: Session) -> dict[tuple[str, str], IntegrationSecret]:
    return {(r.provider, r.name): r for r in session.scalars(select(IntegrationSecret).where(_mine()))}


def version(session: Session) -> str:
    """Changes whenever a connection is added, changed or removed."""
    from superaide.db.models import LlmProvider, ModelRole, OwnerModelPrice

    parts = []
    agent = scope_or_default().agent_id
    for table in (IntegrationSecret, LlmProvider, ModelRole, OwnerModelPrice):
        latest, count = session.execute(
            select(func.max(table.updated_at), func.count(table.id)).where(table.agent_id == agent)
        ).one()
        parts.append(f"{latest}:{count}")
    from superaide.brain import platform_ai

    parts.append(f"credits:{platform_ai.opted_in(session)}")  # opting in or out rebuilds the agent
    # The operator's own Ava (PLATFORM_KEY_OWNER_EMAILS): rebuilt once its owner is known (e.g. right after hiring).
    parts.append(f"server-key:{platform_ai.server_key_is_own(session)}")
    return "|".join(parts)


def env_credentials_apply(settings: Settings | None = None) -> bool:
    """Whether .env credentials stand in for an owner's own: only on a self-hosted server."""
    return (settings or get_settings()).deploy_mode != "hosted"


def runtime_settings(base: Settings | None = None) -> Settings:
    """Settings with the instance's own credentials applied (over .env, when self-hosted)."""
    base = base or get_settings()
    # The platform's key for Superaide AI credits is read from the server's own settings only
    # (brain/platform_ai.platform_key), never carried in an owner's settings.
    updates: dict[str, Any] = {"platform_anthropic_api_key": ""}
    if not env_credentials_apply(base):
        updates.update({field: "" for field in FIELDS.values()})  # BYOK: never the server's keys
    with session_scope() as s:
        from superaide.brain import platform_ai

        if not env_credentials_apply(base) and platform_ai.server_key_is_own(s):
            # The operator's own Ava (PLATFORM_KEY_OWNER_EMAILS): the server's Claude key is its own key.
            env = get_settings()
            updates["anthropic_api_key"] = env.anthropic_api_key or env.platform_anthropic_api_key
        for (provider, name), row in stored(s).items():
            field = FIELDS.get((provider, name))
            if field:
                updates[field] = unseal(row)
        from superaide.brain.providers.config import overlay

        updates.update(overlay(s))  # model providers and roles chosen in the app
        from superaide.brain import platform_ai

        updates["use_platform_ai"] = platform_ai.opted_in(s)  # Superaide AI credits, if the owner chose them
    return base.model_copy(update=updates) if updates else base


def status(session: Session, settings: Settings) -> dict[str, Any]:
    """What the Connections screen shows. Never includes a secret."""
    rows = stored(session)

    def entry(provider: str, names: list[str]) -> dict[str, Any]:
        mine = [rows[(provider, n)] for n in names if (provider, n) in rows]
        from_env = env_credentials_apply() and all(getattr(get_settings(), FIELDS[(provider, n)]) for n in names)
        if mine and len(mine) == len(names):
            return {
                "connected": True,
                "via": "app",
                "hints": {r.name: r.hint for r in mine},
                "meta": mine[0].meta or {},
                "since": min(r.updated_at for r in mine).isoformat(),
            }
        if from_env:
            return {"connected": True, "via": "env", "hints": {}, "meta": {}, "since": None}
        return {"connected": False, "via": None, "hints": {}, "meta": {}, "since": None}

    from superaide.channels import health

    def channel(provider: str, names: list[str]) -> dict[str, Any]:
        found = entry(provider, names)
        # Saved isn't the same as working: say when the service rejected the token or is unreachable.
        found["health"] = health.get(session, provider) if found["connected"] else {"status": health.OK}
        return found

    return {
        "anthropic": entry("anthropic", ["api_key"]),
        "telegram": channel("telegram", ["bot_token"]),
        "slack": channel("slack", ["bot_token", "app_token"]),
        # Google needs the owner's OAuth client first, then their sign-in (the refresh token).
        "google": {
            **entry("google", ["client_id", "client_secret", "refresh_token"]),
            "client_ready": entry("google", ["client_id", "client_secret"])["connected"],
        },
        "whatsapp": {
            "connected": False,
            "available": False,
            "reason": (
                "Meta's WhatsApp Business terms don't allow general-purpose AI assistants on business numbers "
                "outside the EEA and Brazil, so Ava can't use WhatsApp yet."
            ),
        },
    }
