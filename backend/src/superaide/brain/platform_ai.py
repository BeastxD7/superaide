"""Superaide AI credits: the platform's own Claude key, for owners who choose it (ADR 0009 update).

An owner either brings their own model key or opts in to the credits. Their own key always wins
for a role it can serve; the credits only fill roles nothing of theirs can. Every credit-paid call
is recorded with key_source="platform" and reserves against both the owner's allowance
(PLATFORM_OWNER_DAILY_USD / _MONTHLY_USD, rolling) and the platform's daily cap
(PLATFORM_DAILY_CAP_USD), atomically (brain/budget.py).

The key comes only from the server's environment (`platform_key()`), is never put into an owner's
Settings, vault, logs or API responses, and is never used for an owner who didn't opt in.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from superaide.brain.spend import platform_spend
from superaide.platform.audit import audit
from superaide.platform.config import Settings, get_settings
from superaide.platform.db.models import SpendEntry
from superaide.platform.tenancy.context import scope_or_default

log = logging.getLogger(__name__)

ROUTE = "platform"  # the route name in RoutedLLM and the ledger's provider for credit-paid calls


def platform_key(settings: Settings | None = None) -> str:
    """The platform's Claude key, from the server's own environment only. "" when credits are off."""
    env = get_settings()  # never the owner's runtime settings
    if not env.platform_ai_enabled:
        return ""
    return env.platform_anthropic_api_key or env.anthropic_api_key


def available() -> bool:
    """Credits can be offered: the switch is on, there is a key, and the platform's daily cap is above
    zero (a cap of 0 refuses every credit-paid call, so offering them would only mislead)."""
    return bool(platform_key()) and platform_spend.cap_usd() > 0


def warn_if_unusable() -> None:
    """Tell the operator at startup when credits are switched on but can never pay for a call."""
    env = get_settings()
    if env.platform_ai_enabled and platform_key() and platform_spend.cap_usd() <= 0:
        log.warning(
            "PLATFORM_AI_ENABLED is on but PLATFORM_DAILY_CAP_USD is 0, so Superaide AI credits are not offered. "
            "Set PLATFORM_DAILY_CAP_USD above 0 to offer them."
        )


def opted_in(session: Session) -> bool:
    from superaide.platform.tenancy.models import AgentInstance

    instance = session.get(AgentInstance, scope_or_default().agent_id)
    return bool(instance and instance.key_source == "platform")


def set_opt_in(session: Session, on: bool, *, user_id: uuid.UUID | None) -> None:
    from superaide.platform.tenancy.models import AgentInstance

    instance = session.get(AgentInstance, scope_or_default().agent_id)
    if instance is None:
        raise LookupError("no instance in scope")
    instance.key_source = "platform" if on else "owner"
    audit(session, "providers", "platform_credits_opt_in" if on else "platform_credits_opt_out", user_id=user_id)


def allowance(settings: Settings | None = None) -> tuple[Decimal | None, Decimal | None]:
    """The owner's daily and monthly share of the credits (None: no limit of that kind)."""
    env = settings or get_settings()

    def dec(v: float) -> Decimal | None:
        return Decimal(str(v)) if v and v > 0 else None

    return dec(env.platform_owner_daily_usd), dec(env.platform_owner_monthly_usd)


def used(session: Session, agent_id: uuid.UUID | None = None) -> tuple[Decimal, Decimal]:
    """Credits this instance committed in the last 24 hours and 30 days (settled plus reserved)."""
    agent_id = agent_id or scope_or_default().agent_id
    now = datetime.now(UTC)
    amount = case(
        (SpendEntry.status == "settled", SpendEntry.cost_usd),
        (SpendEntry.status == "reserved", SpendEntry.reserved_usd),
        else_=0,
    )

    def since(start: datetime) -> Decimal:
        q = select(func.coalesce(func.sum(amount), 0)).where(
            SpendEntry.agent_id == agent_id, SpendEntry.key_source == "platform", SpendEntry.created_at >= start
        )
        return Decimal(session.scalar(q) or 0)

    return since(now - timedelta(hours=24)), since(now - timedelta(days=30))


def view(session: Session) -> dict[str, Any]:
    """What the AI model page shows about the credits. Never the key."""
    env = get_settings()
    day_cap, month_cap = allowance(env)
    day, month = used(session)
    return {
        "available": available(),
        "opted_in": opted_in(session),
        "model": env.platform_model,
        # None means no limit of that kind for this owner (only the platform's shared cap applies).
        "allowance": {
            "daily_usd": float(day_cap) if day_cap is not None else None,
            "monthly_usd": float(month_cap) if month_cap is not None else None,
        },
        "used": {"today_usd": float(day), "month_usd": float(month)},
    }


@lru_cache(maxsize=4)  # read on every agent build: warn about a bad entry once, not every time
def listed_owner_emails(raw: str) -> frozenset[str]:
    """The addresses in PLATFORM_KEY_OWNER_EMAILS (separated by commas, semicolons or spaces).
    An entry that isn't a valid address is skipped with a warning: one typo in .env must not make
    every agent build and every background job fail."""
    from superaide.auth.service import AuthError, normalize_email

    found: set[str] = set()
    for entry in re.split(r"[,;\s]+", raw):
        if not entry:
            continue
        try:
            found.add(normalize_email(entry))
        except AuthError:
            log.warning("PLATFORM_KEY_OWNER_EMAILS: ignoring %r, which is not a valid email address", entry)
    return frozenset(found)


def server_key_is_own(session: Session) -> bool:
    """The instance in scope belongs to an account listed in PLATFORM_KEY_OWNER_EMAILS (the operator's
    own Ava): it uses the server's ANTHROPIC_API_KEY as its own key."""
    from superaide.platform.db.models import UserAccount
    from superaide.platform.tenancy.models import AgentInstance, InstanceMember

    listed = listed_owner_emails(get_settings().platform_key_owner_emails)
    if not listed:
        return False
    agent_id = scope_or_default().agent_id
    owners: set[uuid.UUID] = set(
        session.scalars(
            select(InstanceMember.user_id).where(InstanceMember.agent_id == agent_id, InstanceMember.role == "owner")
        )
    )
    instance = session.get(AgentInstance, agent_id)
    if instance is not None and instance.owner_user_id:
        owners.add(instance.owner_user_id)
    if not owners:
        return False
    emails = session.scalars(select(UserAccount.email_normalized).where(UserAccount.id.in_(owners)))
    return any(e in listed for e in emails)
