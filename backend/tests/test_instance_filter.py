"""ADR 0009 T7: while an instance is in scope, the app sees only its rows, even where a query
forgot to say so, and even on a connection Row-Level Security doesn't bind (a superuser's)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update

from superaide.context.audience import Audience, visible_memories
from superaide.db.models import ChannelIdentity, Context, Event, MemoryItem, Person
from superaide.db.session import session_scope
from superaide.policy.killswitch import get_state, set_state
from superaide.tenancy.context import InstanceScope, all_instances, use_instance

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())


def seed(scope: InstanceScope, label: str) -> uuid.UUID:
    with use_instance(scope), session_scope() as s:
        person = Person(display_name=f"Owner {label}", relation="manager")
        s.add(person)
        s.flush()
        ctx = Context(provider="telegram", external_id="group-1", kind="group", audience_class="team")
        s.add(ctx)
        s.flush()
        s.add(Event(context_id=ctx.id, direction="in", content=f"{label} said hello", resolved_person_id=person.id))
        for kind in ("public", "org", "persons"):
            s.add(
                MemoryItem(
                    content=f"{label}'s {kind} note", allowed_kind=kind, min_trust=0,
                    allowed_person_ids=[person.id] if kind == "persons" else [], subject_person_ids=[person.id],
                )
            )  # fmt: skip
        return person.id


def test_new_rows_take_the_instance_in_scope_when_created_not_when_flushed():
    with session_scope() as s:
        with use_instance(A):
            person = Person(display_name="Made in A")
        s.add(person)  # flushed later, outside A's scope
    assert (person.agent_id, person.tenant_id) == (A.agent_id, A.tenant_id)


def test_reads_in_scope_see_only_that_instance():
    a_owner, b_owner = seed(A, "A"), seed(B, "B")
    with use_instance(B), session_scope() as s:
        assert [p.display_name for p in s.scalars(select(Person))] == ["Owner B"]
        assert {m.content for m in s.scalars(select(MemoryItem))} == {
            "B's public note",
            "B's org note",
            "B's persons note",
        }
        assert s.get(Person, a_owner) is None  # not even by id
        joined = s.scalars(select(Event).join(Context, Context.id == Event.context_id)).all()
        assert [e.content for e in joined] == ["B said hello"]  # a shared group id, still separate
        assert len(s.scalars(select(Context)).all()) == 1
        assert s.get(Person, b_owner) is not None
    with all_instances(), session_scope() as s:  # platform code that means to see everything
        assert len(s.scalars(select(Person)).all()) == 2


def test_writes_in_scope_touch_only_that_instance():
    seed(A, "A"), seed(B, "B")
    with use_instance(B), session_scope() as s:
        s.execute(update(MemoryItem).values(content="overwritten"))
        s.execute(delete(Event))
    with use_instance(A), session_scope() as s:
        assert "overwritten" not in {m.content for m in s.scalars(select(MemoryItem))}
        assert [e.content for e in s.scalars(select(Event))] == ["A said hello"]


def test_another_owners_public_and_org_notes_never_reach_this_audience():
    seed(A, "A")
    b_owner = seed(B, "B")
    everyone = Audience(person_ids={b_owner}, has_unknown=False, min_trust=2, all_internal=True)
    with use_instance(B), session_scope() as s:
        seen = {m.content for m in visible_memories(s, everyone, limit=50)}
    assert seen and all(c.startswith("B's") for c in seen)


def test_the_same_account_is_a_separate_identity_in_each_instance():
    shared = InstanceScope(tenant_id=A.tenant_id, agent_id=uuid.uuid4())  # same customer, another Ava
    for scope in (A, shared):
        with use_instance(scope), session_scope() as s:
            s.add(ChannelIdentity(provider="telegram", tenant_scope="", stable_key="4242"))
    with all_instances(), session_scope() as s:
        assert len(s.scalars(select(ChannelIdentity).where(ChannelIdentity.stable_key == "4242")).all()) == 2


def test_small_state_follows_the_instance_in_scope():
    with use_instance(A), session_scope() as s:
        set_state(s, "channel_failure_noted:telegram", {"at": "now"})
    with use_instance(B), session_scope() as s:
        assert get_state(s, "channel_failure_noted:telegram") == {}
    with use_instance(A), session_scope() as s:
        assert get_state(s, "channel_failure_noted:telegram") == {"at": "now"}
