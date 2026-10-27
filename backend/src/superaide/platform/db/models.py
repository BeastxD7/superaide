"""Database tables: identity, conversations, audience-gated memory, approvals,
audit, the spend ledger, and user accounts for the app.

Rules baked into the schema:
- Every row carries tenant_id and agent_id.
- Events, link events and audit rows are append-only (the code never updates them).
- A channel identity is unique per (provider, tenant_scope, stable_key) and is
  keyed on the platform's immutable ID, never on a display name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Index,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _tenant() -> uuid.UUID:
    """The customer of the agent instance in scope (ADR 0009); the dev instance when none is."""
    from superaide.platform.tenancy.context import scope_or_default

    return scope_or_default().tenant_id


def _agent() -> uuid.UUID:
    """The agent instance in scope: new rows belong to it (and Row-Level Security checks that they do)."""
    from superaide.platform.tenancy.context import scope_or_default

    return scope_or_default().agent_id


class Base(DeclarativeBase):
    pass


@event.listens_for(Base, "init", propagate=True)
def _belongs_to_the_instance_in_scope(target: Any, args: Any, kwargs: dict[str, Any]) -> None:
    """A new row takes the agent instance in scope when it is created, not when it is flushed
    (which can be after the code has left that instance's scope). Values passed explicitly win."""
    mapper_columns = type(target).__mapper__.columns
    if "agent_id" in mapper_columns and "agent_id" not in kwargs:
        kwargs["agent_id"] = _agent()
    if "tenant_id" in mapper_columns and "tenant_id" not in kwargs and "agent_id" in mapper_columns:
        kwargs["tenant_id"] = _tenant()
    elif "tenant_id" in mapper_columns and "tenant_id" not in kwargs and type(target).__tablename__ != "tenant":
        kwargs["tenant_id"] = _tenant()


class Scoped:
    """Columns shared by every table."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_tenant, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_agent, index=True)
    # Set in Python so rows written in one transaction keep their order
    # (Postgres now() is the same for a whole transaction).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


# --- Identity -----------------------------------------------------------------


# --- Conversations ---------------------------------------------------------------


# --- Memory ------------------------------------------------------------------------


# --- The owner's people and preferences ----------------------------------------------


# --- Governance ----------------------------------------------------------------


class AuditLog(Scoped, Base):
    """Append-only record of decisions, model calls and refused actions."""

    __tablename__ = "audit_log"
    __table_args__ = (
        # The safety log and the owner's access log, newest first (ADR 0014); the second serves only the operator's
        # own visits, a small slice of a log that mostly holds model calls and identity checks.
        Index("ix_audit_log_agent_created", "agent_id", text("created_at DESC"), text("id DESC")),
        Index(
            "ix_audit_log_operator_access",
            "agent_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("actor = 'operator' AND action = 'operator_access'"),
        ),
    )

    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# --- User accounts and sessions ----------------------------------------------------


class TenantScoped:
    """Columns for tables that belong to the tenant rather than to one agent."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_tenant, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


EMAIL_MATCH_SQL = "octet_length(email) <> char_length(email) OR email_normalized = lower(btrim(email))"


# --- Connections (encrypted third-party credentials) --------------------------------


class AgentState(Base):
    """Small key-value store for switches such as the kill switch."""

    __tablename__ = "agent_state"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_tenant)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_agent)
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Tables defined in their own modules, imported here so every user of Base (Alembic,
# the tests' create_all) sees them.
from superaide.actionlog import models as _actionlog_models  # noqa: E402, F401
from superaide.approvals import models as _approvals_models  # noqa: E402, F401
from superaide.asklink import models as _asklink_models  # noqa: E402, F401
from superaide.brain import models as _brain_models  # noqa: E402, F401
from superaide.features.brainshare import models as _brainshare_models  # noqa: E402, F401
from superaide.features.imports import models as _imports_models  # noqa: E402, F401
from superaide.features.journal import models as _features_journal_models  # noqa: E402, F401
from superaide.features.memsocket import models as _memsocket_models  # noqa: E402, F401
from superaide.features.mirror import models as _features_mirror_models  # noqa: E402, F401
from superaide.features.push import models as _push_models  # noqa: E402, F401
from superaide.features.rehearsal import models as _rehearsal_models  # noqa: E402, F401
from superaide.features.school import models as _school_models  # noqa: E402, F401
from superaide.identity import models as _identity_models  # noqa: E402, F401
from superaide.identity.auth import models as _auth_models  # noqa: E402, F401
from superaide.identity.principal import models as _principal_models  # noqa: E402, F401
from superaide.integrations.calendar import models as _integrations_calendar_models  # noqa: E402, F401
from superaide.intentions import models as _intentions_models  # noqa: E402, F401
from superaide.jobs import models as _jobs_models  # noqa: E402, F401
from superaide.mandate import models as _mandate_models  # noqa: E402, F401
from superaide.mcp import models as _mcp_models  # noqa: E402, F401
from superaide.memory import models as _memory_models  # noqa: E402, F401
from superaide.platform.tenancy import models as _tenancy_models  # noqa: E402, F401
from superaide.reception import models as _reception_models  # noqa: E402, F401
from superaide.skills import models as _skills_models  # noqa: E402, F401
from superaide.tooltrace import models as _tooltrace_models  # noqa: E402, F401
from superaide.triage import models as _triage_models  # noqa: E402, F401
from superaide.trust import models as _trust_models  # noqa: E402, F401

# Where each table class lives now (ADR 0015 phase 7). `from superaide.platform.db.models import Person` keeps
# working for one release through this lazy lookup; new code imports from the owning module.
_MOVED = {
    "AccountAudit": "superaide.identity.auth.models",
    "ActionLog": "superaide.actionlog.models",
    "AgentHire": "superaide.identity.auth.models",
    "Approval": "superaide.approvals.models",
    "AuthEvent": "superaide.identity.auth.models",
    "CalendarWatch": "superaide.integrations.calendar.models",
    "ChannelIdentity": "superaide.identity.models",
    "ChannelInvite": "superaide.identity.models",
    "ConsolidationRun": "superaide.memory.models",
    "Contact": "superaide.memory.models",
    "Context": "superaide.memory.models",
    "Document": "superaide.memory.models",
    "DocumentChunk": "superaide.memory.models",
    "Episode": "superaide.memory.models",
    "Event": "superaide.memory.models",
    "IdentityClaim": "superaide.identity.models",
    "IdentityLinkEvent": "superaide.identity.models",
    "InstalledSkill": "superaide.skills.models",
    "IntegrationSecret": "superaide.identity.auth.models",
    "Intention": "superaide.intentions.models",
    "Job": "superaide.jobs.models",
    "JournalSetting": "superaide.features.journal.models",
    "LinkChallenge": "superaide.identity.models",
    "LlmProvider": "superaide.brain.models",
    "Mandate": "superaide.mandate.models",
    "McpPin": "superaide.mcp.models",
    "MemoryImport": "superaide.memory.models",
    "MemoryItem": "superaide.memory.models",
    "MemoryOrigin": "superaide.memory.models",
    "MirrorNote": "superaide.features.mirror.models",
    "MirrorSetting": "superaide.features.mirror.models",
    "ModelRole": "superaide.brain.models",
    "Onboarding": "superaide.memory.models",
    "OwnerModelPrice": "superaide.brain.models",
    "OwnerPreference": "superaide.memory.models",
    "Person": "superaide.identity.models",
    "Prediction": "superaide.features.journal.models",
    "SkillRelease": "superaide.skills.models",
    "SkillTombstone": "superaide.skills.models",
    "SpendEntry": "superaide.brain.models",
    "ToolTrace": "superaide.tooltrace.models",
    "UserAccount": "superaide.identity.auth.models",
    "UserSession": "superaide.identity.auth.models",
}


def __getattr__(name: str) -> Any:
    home = _MOVED.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(home), name)
