from typing import Any

from superaide.platform.db.models import Base
from superaide.platform.db.session import get_engine, session_scope

__all__ = [
    "AgentHire",
    "AgentState",
    "Approval",
    "AuditLog",
    "AccountAudit",
    "AuthEvent",
    "Base",
    "ChannelIdentity",
    "ChannelInvite",
    "Contact",
    "Context",
    "Document",
    "Event",
    "IdentityLinkEvent",
    "IntegrationSecret",
    "LinkChallenge",
    "MemoryItem",
    "Onboarding",
    "OwnerPreference",
    "Person",
    "SpendEntry",
    "UserAccount",
    "UserSession",
    "get_engine",
    "session_scope",
]


def __getattr__(name: str) -> Any:
    """`from superaide.platform.db import Person` keeps working for one release (the class lives in its module now,
    ADR 0015 phase 7). Lazy, so importing this package never pulls in every table before `Base` exists."""
    from superaide.platform.db import models

    return getattr(models, name)
