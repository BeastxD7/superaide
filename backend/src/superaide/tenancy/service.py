"""Creating and finding agent instances (ADR 0009, work package T0).

The first hire on a server takes the development instance (the ids every existing
row carries); with ALLOW_MANY_OWNERS on, each later hire gets its own (T2).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from superaide.config import DEFAULT_AGENT_ID, DEFAULT_TENANT_ID, get_settings
from superaide.db.models import Person, UserAccount
from superaide.tenancy.context import InstanceScope, default_scope, scope_or_default
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


class AlreadyTaken(Exception):
    """Ava already works for another account, and many owners aren't switched on yet."""

    def __init__(self, owner_name: str) -> None:
        super().__init__(owner_name)
        self.owner_name = owner_name


def membership(session: Session, user_id: uuid.UUID, agent_slug: str) -> tuple[AgentInstance, InstanceMember] | None:
    """The account's active instance of this agent, if it hired one."""
    return next(((i, m) for i, m in instances_of(session, user_id) if i.agent_slug == agent_slug), None)


def place_hire(session: Session, user: UserAccount, agent_slug: str) -> InstanceScope:
    """Which instance a hire goes to, creating its tenant and instance rows when new.

    The first hire on a server takes the development instance whose ids existing rows
    and still-global code (channels, kill switch) use, so a single-owner server works
    exactly as before. Later hires get an instance of their own when ALLOW_MANY_OWNERS
    is on; otherwise they are refused with AlreadyTaken.
    """
    if (mine := membership(session, user.id, agent_slug)) is not None:
        return scope_of(mine[0])
    first = default_scope()
    instance = session.get(AgentInstance, first.agent_id)
    if instance is None or instance.owner_user_id == user.id:
        return scope_of(_create(session, user, agent_slug, first))
    if not get_settings().allow_many_owners:
        owner = session.get(UserAccount, instance.owner_user_id)
        raise AlreadyTaken(owner.display_name if owner else "")
    return scope_of(_create(session, user, agent_slug, InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())))


def _create(session: Session, user: UserAccount, agent_slug: str, scope: InstanceScope) -> AgentInstance:
    if session.get(Tenant, scope.tenant_id) is None:
        session.add(
            Tenant(id=scope.tenant_id, name=user.display_name, default_timezone=user.timezone or "UTC", kind="personal")
        )
        session.flush()
    instance = session.get(AgentInstance, scope.agent_id)
    if instance is None:
        instance = AgentInstance(
            id=scope.agent_id,
            tenant_id=scope.tenant_id,
            agent_slug=agent_slug,
            display_name=agent_slug.capitalize(),
            owner_user_id=user.id,
            timezone=user.timezone or "UTC",
        )
        session.add(instance)
        session.flush()
    return instance


def join_as_owner(session: Session, user: UserAccount, scope: InstanceScope) -> Person:
    """The account's Person inside this instance (People are never shared between instances),
    recorded as the instance's owner member. Run with `scope` in scope."""
    member = session.scalar(
        select(InstanceMember).where(InstanceMember.agent_id == scope.agent_id, InstanceMember.user_id == user.id)
    )
    if member is not None:
        person = session.get(Person, member.person_id)
        assert person is not None
        return person
    from superaide.tenancy.context import all_instances

    with all_instances():  # the sign-up Person may live in another instance (where sign-up happened)
        person = session.get(Person, user.person_id)
    if person is None or person.agent_id != scope.agent_id:
        # The Person made at sign-up lives in whichever instance was in scope then; the owner gets
        # a fresh one here, and the unused sign-up one is removed.
        old = person
        person = Person(
            display_name=user.display_name, relation="unknown", channel_prefs={"routine": "web"},
            tenant_id=scope.tenant_id, agent_id=scope.agent_id,
        )  # fmt: skip
        session.add(person)
        session.flush()
        user.person_id = person.id
        session.flush()
        if old is not None:
            _drop_if_unused(session, old)
    session.add(InstanceMember(agent_id=scope.agent_id, user_id=user.id, person_id=person.id, role="owner"))
    session.flush()
    return person


def _drop_if_unused(session: Session, person: Person) -> None:
    from superaide.db.models import ChannelIdentity, Onboarding
    from superaide.db.session import rescope
    from superaide.tenancy.context import use_instance

    with use_instance(InstanceScope(tenant_id=person.tenant_id, agent_id=person.agent_id)):
        rescope(session)
        used = session.scalar(select(ChannelIdentity.id).where(ChannelIdentity.person_id == person.id).limit(1))
        used = used or session.scalar(select(Onboarding.id).where(Onboarding.person_id == person.id).limit(1))
        used = used or session.scalar(select(InstanceMember.id).where(InstanceMember.person_id == person.id).limit(1))
        if used is None:
            session.delete(person)
            session.flush()
    rescope(session)


def instance_owner(session: Session, scope: InstanceScope | None = None) -> tuple[UserAccount, Person] | None:
    """The account that hired the instance in scope, and its Person there."""
    scope = scope or scope_or_default()
    instance = session.get(AgentInstance, scope.agent_id)
    if instance is None or instance.status != "active":
        return None
    member = session.scalar(
        select(InstanceMember).where(
            InstanceMember.agent_id == instance.id,
            InstanceMember.user_id == instance.owner_user_id,
            InstanceMember.role == "owner",
        )
    )
    account = session.get(UserAccount, instance.owner_user_id)
    person = session.get(Person, member.person_id) if member else None
    return (account, person) if account and person else None


def instances_of(session: Session, user_id: uuid.UUID) -> list[tuple[AgentInstance, InstanceMember]]:
    """The active instances this account belongs to, oldest first."""
    rows = session.execute(
        select(AgentInstance, InstanceMember)
        .join(InstanceMember, InstanceMember.agent_id == AgentInstance.id)
        .where(InstanceMember.user_id == user_id, AgentInstance.status == "active")
        .order_by(AgentInstance.created_at)
    ).all()
    return [(i, m) for i, m in rows]


def active_scopes(session: Session) -> list[InstanceScope]:
    """Every hired instance on the server (the worker serves them all)."""
    rows = session.scalars(
        select(AgentInstance).where(AgentInstance.status == "active").order_by(AgentInstance.created_at)
    )
    return [scope_of(i) for i in rows]


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
