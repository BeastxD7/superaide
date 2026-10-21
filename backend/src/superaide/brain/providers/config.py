"""The owner's model settings for this Ava: connected providers, their keys and the model per role.

Rows carry tenant_id/agent_id (each Ava has its own); keys live in the vault under
`llm:<key>/api_key` and are never returned. Each endpoint has a key (its preset, or e.g.
"custom-2" for another of the same kind). Claude keeps its existing vault slot
(`anthropic/api_key`), which `.env`'s ANTHROPIC_API_KEY backs as the platform default.
`overlay()` turns all of this into Settings fields the agent is built from; a role on a
provider other than Claude is written as `<key>::<model>`, which brain/llm.py routes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.brain.providers.presets import PRESETS
from superaide.db.models import IntegrationSecret, LlmProvider, ModelRole
from superaide.vault.crypto import decrypt, encrypt, mask

ROLES = ("chat", "triage", "deep")
ROLE_FIELDS = {"chat": "model_chat", "triage": "model_triage", "deep": "model_deep"}
SEP = "::"


KEY_RE = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


def slot_provider(key: str) -> str:
    return f"llm:{key}"


def qualified(provider: str, model: str) -> str:
    return model if provider == "anthropic" else f"{provider}{SEP}{model}"


def split(model: str) -> tuple[str, str]:
    """`openrouter::acme/fast` -> ("openrouter", "acme/fast"); a bare id is Claude's."""
    if SEP in model:
        provider, name = model.split(SEP, 1)
        return provider, name
    return "anthropic", model


def _secret(session: Session, key: str) -> IntegrationSecret | None:
    return session.scalar(
        select(IntegrationSecret).where(
            IntegrationSecret.provider == slot_provider(key), IntegrationSecret.name == "api_key"
        )
    )


def key_for(session: Session, key: str) -> str:
    row = _secret(session, key)
    return decrypt(row.ciphertext, f"{slot_provider(key)}/api_key") if row else ""


def endpoint(session: Session, key: str) -> LlmProvider | None:
    return session.scalar(select(LlmProvider).where(LlmProvider.key == key))


def save_provider(
    session: Session,
    preset: str,
    *,
    key: str | None = None,
    base_url: str,
    api_key: str | None,
    user_id: uuid.UUID | None,
    models: list[str],
    prices: dict[str, dict[str, str]] | None = None,
) -> LlmProvider:
    """Create or update an endpoint (key defaults to the preset). `api_key=None` keeps the stored key."""
    if preset not in PRESETS:
        raise ValueError(f"unknown provider {preset!r}")
    key = key or preset
    now = datetime.now(UTC)
    row = endpoint(session, key)
    if row is None:
        row = LlmProvider(preset=preset, key=key)
        session.add(row)
    elif row.preset != preset:
        raise ValueError(f"{key!r} is already a {row.preset} endpoint")
    row.base_url, row.enabled, row.set_by_user_id, row.updated_at = base_url, True, user_id, now
    row.meta = {"models": models[:1000], "checked_at": now.isoformat()}
    if prices:
        row.meta["prices"] = {m: p for m, p in prices.items() if m in set(models[:1000])}
    if api_key is not None:
        secret = _secret(session, key)
        if api_key == "":
            if secret is not None:
                session.delete(secret)
        else:
            if secret is None:
                secret = IntegrationSecret(provider=slot_provider(key), name="api_key", ciphertext="")
                session.add(secret)
            secret.ciphertext = encrypt(api_key, f"{slot_provider(key)}/api_key")
            secret.hint, secret.meta, secret.set_by_user_id = mask(api_key), {}, user_id
            secret.validated_at = secret.updated_at = now
    session.flush()
    audit(
        session, "providers", "provider_saved", provider=key, preset=preset, key_changed=api_key is not None,
        user_id=user_id,
    )  # fmt: skip
    return row


def remove_provider(session: Session, key: str, *, user_id: uuid.UUID | None) -> None:
    for row in session.scalars(select(LlmProvider).where(LlmProvider.key == key)):
        session.delete(row)
    secret = _secret(session, key)
    if secret is not None:
        session.delete(secret)
    # Roles on this endpoint fall back to the default (Claude, from .env or Connections).
    for role in session.scalars(select(ModelRole).where(ModelRole.provider == key)):
        session.delete(role)
    audit(session, "providers", "provider_removed", provider=key, user_id=user_id)


def set_role(session: Session, role: str, provider: str, model: str, *, user_id: uuid.UUID | None) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    row = session.scalar(select(ModelRole).where(ModelRole.role == role))
    if row is None:
        row = ModelRole(role=role)
        session.add(row)
    row.provider, row.model, row.set_by_user_id, row.updated_at = provider, model, user_id, datetime.now(UTC)
    session.flush()
    audit(session, "providers", "role_changed", role=role, provider=provider, model=model, user_id=user_id)


def overlay(session: Session) -> dict[str, Any]:
    """Settings fields from the owner's model settings (applied over .env)."""
    providers: dict[str, dict[str, str]] = {}
    for row in session.scalars(select(LlmProvider).where(LlmProvider.enabled.is_(True))):
        providers[row.key] = {"preset": row.preset, "base_url": row.base_url, "api_key": key_for(session, row.key)}
    updates: dict[str, Any] = {"llm_providers": providers}
    for role in session.scalars(select(ModelRole)):
        if role.provider == "anthropic" or role.provider in providers:
            updates[ROLE_FIELDS[role.role]] = qualified(role.provider, role.model)
    return updates


def view(session: Session) -> dict[str, Any]:
    """What the model settings page shows. Never includes a key."""
    providers = []
    for row in session.scalars(select(LlmProvider).order_by(LlmProvider.created_at)):
        secret = _secret(session, row.key)
        providers.append(
            {
                "key": row.key,
                "preset": row.preset,
                "base_url": row.base_url,
                "enabled": row.enabled,
                "key_hint": secret.hint if secret else "",
                "models": (row.meta or {}).get("models", []),
                "checked_at": (row.meta or {}).get("checked_at"),
            }
        )
    roles = {r.role: {"provider": r.provider, "model": r.model} for r in session.scalars(select(ModelRole))}
    return {"providers": providers, "roles": roles}


def listed_prices(session: Session) -> dict[tuple[str, str], Any]:
    """Prices providers listed for their models when they were connected (OpenRouter)."""
    from decimal import Decimal

    from superaide.brain.pricing import ModelPrice

    out: dict[tuple[str, str], Any] = {}
    for row in session.scalars(select(LlmProvider)):
        for model, p in ((row.meta or {}).get("prices") or {}).items():
            inp, outp = Decimal(p["input"]), Decimal(p["output"])
            read = Decimal(p["cache_read"]) / inp if p.get("cache_read") and inp else Decimal(1)
            out[(row.key, model)] = ModelPrice(inp, outp, read)
    return out
