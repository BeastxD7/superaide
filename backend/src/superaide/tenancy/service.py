"""Creating and finding agent instances (ADR 0009, work package T0).

Until the hire flow becomes per instance (T2), every hire maps to the single
development instance whose ids all existing rows carry, so the new tables stay
consistent with today's data from the first hire on.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from superaide.config import DEFAULT_AGENT_ID, DEFAULT_TENANT_ID
from superaide.db.models import Person, UserAccount
from superaide.tenancy.context import InstanceScope, default_scope
from superaide.tenancy.models import AgentInstance, InstanceMember, Tenant

# Tables whose rows carry tenant_id and agent_id. The backfill refuses to guess when
# they hold more than one of either (that would mean a second tenant already exists).
SCOPED_TABLES = (
    "person",
    "channel_identity",
    "context",
    "event",
    "memory_item",
    "document",
    "contact",
    "owner_preference",
    "approval",
    "audit_log",
    "spend_ledger",
    "onboarding",
    "job",
    "intention",
    "installed_skill",
    "llm_provider",
    "model_role",
)


class AmbiguousTenancy(RuntimeError):
    """The data already holds more than one tenant or agent id; a person must decide."""


def ensure_instance_for_hire(session: Session, user: UserAccount, person: Person, agent_slug: str) -> AgentInstance:
    """The instance behind a hire, with the hirer as its owner member. Safe to call again."""
    scope = default_scope()
    tenant = session.get(Tenant, scope.tenant_id)
    if tenant is None:
        tenant = Tenant(
            id=scope.tenant_id, name=user.display_name, default_timezone=user.timezone or "UTC", kind="personal"
        )
        session.add(tenant)
        session.flush()
    instance = session.get(AgentInstance, scope.agent_id)
    if instance is None:
        instance = AgentInstance(
            id=scope.agent_id,
            tenant_id=tenant.id,
            agent_slug=agent_slug,
            display_name=agent_slug.capitalize(),
            owner_user_id=user.id,
            timezone=user.timezone or "UTC",
        )
        session.add(instance)
        session.flush()
    member = session.scalar(
        select(InstanceMember).where(InstanceMember.agent_id == instance.id, InstanceMember.user_id == user.id)
    )
    if member is None:
        session.add(InstanceMember(agent_id=instance.id, user_id=user.id, person_id=person.id, role="owner"))
        session.flush()
    return instance


def instances_of(session: Session, user_id: uuid.UUID) -> list[tuple[AgentInstance, InstanceMember]]:
    """The active instances this account belongs to, oldest first."""
    rows = session.execute(
        select(AgentInstance, InstanceMember)
        .join(InstanceMember, InstanceMember.agent_id == AgentInstance.id)
        .where(InstanceMember.user_id == user_id, AgentInstance.status == "active")
        .order_by(AgentInstance.created_at)
    ).all()
    return [(i, m) for i, m in rows]


def scope_of(instance: AgentInstance) -> InstanceScope:
    return InstanceScope(tenant_id=instance.tenant_id, agent_id=instance.id)


# --- the one-off migration of today's single development tenant -----------------------


def _distinct(conn: Connection, column: str) -> set[Any]:
    values: set[Any] = set()
    for table in SCOPED_TABLES:
        values.update(
            conn.execute(text(f"SELECT DISTINCT {column} FROM {table}")).scalars()
        )  # table names are the fixed list above
    return values


def backfill_default_instance(conn: Connection) -> int:
    """Create the tenant, instance and owner membership for the existing active hire(s).

    Plain SQL on a connection, so the Alembic migration and the tests run the same
    code. Rewrites no existing row. Returns how many instances it created.
    """
    tenants, agents = _distinct(conn, "tenant_id"), _distinct(conn, "agent_id")
    if len(tenants) > 1 or len(agents) > 1:
        raise AmbiguousTenancy(f"found {len(tenants)} tenant ids and {len(agents)} agent ids; migrate by hand")
    agent_id = next(iter(agents), DEFAULT_AGENT_ID)
    hires = conn.execute(
        text(
            "SELECT h.tenant_id, h.agent_slug, h.owner_user_id, h.created_at, u.display_name, u.timezone, u.person_id "
            "FROM agent_hire h JOIN user_account u ON u.id = h.owner_user_id "
            "WHERE h.status = 'active' ORDER BY h.created_at"
        )
    ).all()
    if len(hires) > 1:
        raise AmbiguousTenancy(f"found {len(hires)} active hires; only one can own the existing data")
    created = 0
    for tenant_id, slug, owner_id, since, name, tz, person_id in hires:
        if tenants and tenant_id not in tenants:
            raise AmbiguousTenancy("the hire's tenant differs from the data's tenant")
        tenant_id = tenant_id or DEFAULT_TENANT_ID
        conn.execute(
            text(
                "INSERT INTO tenant (id, kind, name, region, default_locale, default_timezone, billing_currency, "
                "plan, status, created_at) VALUES (:id, 'personal', :name, '', 'en', :tz, 'USD', '', 'active', :at) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "name": name or "", "tz": tz or "UTC", "at": since},
        )
        inserted = conn.execute(
            text(
                "INSERT INTO agent_instance (id, tenant_id, agent_slug, display_name, owner_user_id, status, region, "
                "locale, timezone, key_source, created_at) VALUES (:id, :tenant, :slug, :display, :owner, 'active', "
                "'', 'en', :tz, 'owner', :at) ON CONFLICT (id) DO NOTHING RETURNING id"
            ),
            {
                "id": agent_id,
                "tenant": tenant_id,
                "slug": slug,
                "display": slug.capitalize(),
                "owner": owner_id,
                "tz": tz or "UTC",
                "at": since,
            },
        ).scalar()
        created += 1 if inserted else 0
        conn.execute(
            text(
                "INSERT INTO instance_member (id, agent_id, user_id, person_id, role, created_at) "
                "VALUES (:id, :agent, :user, :person, 'owner', :at) ON CONFLICT ON CONSTRAINT uq_instance_member "
                "DO NOTHING"
            ),
            {"id": uuid.uuid4(), "agent": agent_id, "user": owner_id, "person": person_id, "at": since},
        )
    return created
