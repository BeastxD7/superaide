"""Saving, reading and applying the owner's connections.

Credentials saved in the app take precedence over the same setting in .env.
`runtime_settings()` is the Settings with those credentials applied; the agent
and the channel listeners are rebuilt from it whenever a connection changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agi_agent.audit import audit
from agi_agent.config import Settings, get_settings
from agi_agent.db.models import IntegrationSecret
from agi_agent.db.session import session_scope
from agi_agent.vault.crypto import decrypt, encrypt, mask

# Which stored credential fills which setting.
FIELDS: dict[tuple[str, str], str] = {
    ("anthropic", "api_key"): "anthropic_api_key",
    ("telegram", "bot_token"): "telegram_bot_token",
    ("slack", "bot_token"): "slack_bot_token",
    ("slack", "app_token"): "slack_app_token",
    ("slack", "team_id"): "slack_team_id",
}
SECRET_NAMES = {"api_key", "bot_token", "app_token"}  # team_id is an identifier, not a secret


def slot(provider: str, name: str) -> str:
    return f"{provider}/{name}"


def save(
    session: Session, provider: str, values: dict[str, str], *, user_id: uuid.UUID | None, meta: dict[str, Any]
) -> None:
    now = datetime.now(UTC)
    for name, value in values.items():
        if (provider, name) not in FIELDS:
            raise ValueError(f"unknown setting {provider}/{name}")
        row = session.scalar(
            select(IntegrationSecret).where(IntegrationSecret.provider == provider, IntegrationSecret.name == name)
        )
        if row is None:
            row = IntegrationSecret(provider=provider, name=name, ciphertext="")
            session.add(row)
        row.ciphertext = encrypt(value, slot(provider, name))
        row.hint = mask(value) if name in SECRET_NAMES else value[:40]
        row.meta = meta
        row.set_by_user_id = user_id
        row.validated_at = now
        row.updated_at = now
    # Never the value: only which slots changed and who changed them.
    audit(session, "connections", "connected", provider=provider, fields=sorted(values), user_id=user_id)


def remove(session: Session, provider: str, *, user_id: uuid.UUID | None) -> int:
    rows = session.scalars(select(IntegrationSecret).where(IntegrationSecret.provider == provider)).all()
    for r in rows:
        session.delete(r)
    audit(session, "connections", "disconnected", provider=provider, user_id=user_id)
    return len(rows)


def stored(session: Session) -> dict[tuple[str, str], IntegrationSecret]:
    return {(r.provider, r.name): r for r in session.scalars(select(IntegrationSecret))}


def version(session: Session) -> str:
    """Changes whenever a connection is added, changed or removed."""
    latest, count = session.execute(
        select(func.max(IntegrationSecret.updated_at), func.count(IntegrationSecret.id))
    ).one()
    return f"{latest}:{count}"


def runtime_settings(base: Settings | None = None) -> Settings:
    """Settings with the credentials saved in the app applied over .env."""
    base = base or get_settings()
    updates: dict[str, Any] = {}
    with session_scope() as s:
        for (provider, name), row in stored(s).items():
            field = FIELDS.get((provider, name))
            if field:
                updates[field] = decrypt(row.ciphertext, slot(provider, name))
    return base.model_copy(update=updates) if updates else base


def status(session: Session, settings: Settings) -> dict[str, Any]:
    """What the Connections screen shows. Never includes a secret."""
    rows = stored(session)

    def entry(provider: str, names: list[str]) -> dict[str, Any]:
        mine = [rows[(provider, n)] for n in names if (provider, n) in rows]
        from_env = all(getattr(get_settings(), FIELDS[(provider, n)]) for n in names)
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

    return {
        "anthropic": entry("anthropic", ["api_key"]),
        "telegram": entry("telegram", ["bot_token"]),
        "slack": entry("slack", ["bot_token", "app_token"]),
        "whatsapp": {
            "connected": False,
            "available": False,
            "reason": (
                "Meta's WhatsApp Business terms don't allow general-purpose AI assistants on business numbers "
                "outside the EEA and Brazil, so Ava can't use WhatsApp yet."
            ),
        },
    }
