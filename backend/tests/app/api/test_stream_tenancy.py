"""ADR 0009: a streamed chat turn (`POST /app/chat/stream`) works inside its owner's instance.

`stream_turn` starts the turn's thread when the response body is first read, after the endpoint
has returned and the `owner_person_id` dependency (which sets the instance scope) may have been
torn down. These tests run two owners through the real route and the real runtime (the agent is
built and cached per instance, as in production) and check that every read and write of the
second owner's turn lands in the second owner's instance only: the events, the memory fact, the
spend rows, the instance the model is called under, and the prompt it is given. The first
owner's data must not change at all. Done one request after the other and at the same moment.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetGuard
from superaide.brain.llm import FakeLLM
from superaide.brain.pricing import UsageCounts
from superaide.channels.base import FakeMessenger
from superaide.config import get_settings
from superaide.db.models import Base, Event, MemoryItem, Person, Scoped, SpendEntry
from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.pipeline import Agent
from superaide.tenancy.context import all_instances, scope_or_default
from tests.api_support import OTHER, OWNER, hire, signup
from tests.app.test_stream import events_of
from tests.tenancy.api.test_many_owners import instance_of


@pytest.fixture
def many(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)


# Markers carry a non-hex prefix (zq): random hex ids in responses can contain a plain number by chance.
BIKE = "my bike lock code is zq9032"  # the second owner's secret
LOCKER = "my locker code is zq4417"  # the first owner's secret
ASK_B = "What is my bike lock code?"
ASK_A = "What is my locker code?"


@dataclass
class Tape:
    """What the fake model saw: which instance each call ran under, and its prompt."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    built: list[uuid.UUID] = field(default_factory=list)  # instance in scope when an agent was built
    calls: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)  # (scope, prompt, last message)
    meet: threading.Barrier | None = None  # both turns are inside the model call at once
    met: set[tuple[int, uuid.UUID]] = field(default_factory=set)


@pytest.fixture
def tape() -> Tape:
    return Tape()


@pytest.fixture
def app(tape, monkeypatch):
    """The real app and runtime (an agent per instance), with a model that costs nothing but,
    like the real one, books each call to the ledger of the instance its agent was built for."""
    from superaide import factory

    def responder(purpose, system, messages, output_model):
        last = messages[-1]["content"] if messages else ""
        last = last if isinstance(last, str) else " ".join(str(b.get("text", b)) for b in last)
        prompt = "\n".join(b.text for b in system) + "\n" + "\n".join(str(m["content"]) for m in messages)
        with tape.lock:
            tape.calls.append((scope_or_default().agent_id, prompt, last))
        meet = tape.meet
        if meet is not None and (ASK_A in last or ASK_B in last):
            with tape.lock:
                first = (id(meet), scope_or_default().agent_id) not in tape.met
                tape.met.add((id(meet), scope_or_default().agent_id))
            if first:  # once per turn, on its first question: wait until the other owner's turn is here too
                try:
                    meet.wait(timeout=20)
                except threading.BrokenBarrierError:
                    pass
        return "Noted."

    class BookedLLM(FakeLLM):
        def __init__(self, guard: BudgetGuard) -> None:
            super().__init__(responder)
            self.guard = guard

        def step(self, **kw: Any):
            entry = self.guard.reserve(purpose=kw["purpose"], model="claude-haiku-5-5", worst_case_usd=Decimal("0.01"))
            result = super().step(**kw)
            self.guard.settle(entry, model="claude-haiku-5-5", usage=UsageCounts(input_tokens=10), request_id=None)
            return result

    def build_agent(settings=None):
        settings = settings or get_settings()
        with tape.lock:
            tape.built.append(scope_or_default().agent_id)
        guard = BudgetGuard.for_instance(settings)  # the instance in scope when the agent is built
        return Agent(
            llm=BookedLLM(guard),
            messengers={"telegram": FakeMessenger(), "slack": FakeMessenger(), "web": FakeMessenger()},
            settings=settings,
        )

    monkeypatch.setattr(factory, "build_agent", build_agent)
    return create_app(None)  # no fixed agent: the runtime builds one per instance


def counts(agent_id: uuid.UUID) -> Counter[str]:
    """How many rows this instance has in every table that carries an instance."""
    out: Counter[str] = Counter()
    with all_instances(), session_scope() as s:
        for table in Base.metadata.sorted_tables:
            if "agent_id" not in table.c:
                continue
            n = s.execute(select(table.c.agent_id).where(table.c.agent_id == agent_id)).all()
            out[table.name] = len(n)
    return out


def stream(client, text: str) -> list[tuple[str, dict[str, Any]]]:
    with client.stream("POST", "/app/chat/stream", json={"text": text}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    events = events_of(body)
    assert events[-1][0] == "done", events
    return events


def tell(client, text: str) -> None:
    assert stream(client, text)[-1][1]["reply"]


def rows_of(model: type[Scoped], **where: Any) -> list[Any]:
    with all_instances(), session_scope() as s:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        rows = list(s.scalars(q))
        s.expunge_all()
        return rows


def assert_turn_stayed_in(tape: Tape, mine, theirs, secret: str, other_secret: str, ask: str) -> None:
    """Everything the turn that asked `ask` did belongs to instance `mine`, none to `theirs`."""
    # The model was called under this owner's instance, never the other's.
    asked = [c for c in tape.calls if ask in c[2]]
    assert asked, "the model was never called for this question"
    assert {c[0] for c in asked} == {mine}
    # The prompt it was given has this owner's notes and nothing of the other owner's.
    assert all(other_secret not in c[1] for c in asked)
    assert any(secret in c[1] for c in asked)
    # The chat events (the question and Ava's answer) are this instance's.
    questions = [e for e in rows_of(Event) if e.content == ask]
    assert len(questions) == 1 and questions[0].agent_id == mine
    chat = rows_of(Event, context_id=questions[0].context_id)  # the whole conversation it sits in
    assert {e.content for e in chat} >= {ask, "Noted."}
    assert {e.agent_id for e in chat} == {mine}
    # The memory fact.
    facts = [m for m in rows_of(MemoryItem) if secret in m.content]
    assert len(facts) == 1 and facts[0].agent_id == mine
    assert not [m for m in rows_of(MemoryItem, agent_id=theirs) if secret in m.content]
    # The model call was booked to this instance's ledger.
    assert any(e.agent_id == mine and e.status == "settled" for e in rows_of(SpendEntry))


def two_owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def test_a_second_owners_streamed_turn_stays_in_their_instance(browser, many, tape):
    a, b = two_owners(browser)
    ia, ib = instance_of(OWNER["email"]).id, instance_of(OTHER["email"]).id
    assert ia != ib

    # The first owner chats first (so the default instance has data, and a cached agent).
    tell(a, f"Remember that {LOCKER}")
    tell(a, ASK_A)
    first = counts(ia)
    spend_a = [e.id for e in rows_of(SpendEntry, agent_id=ia)]
    assert first["spend_ledger"] and first["memory_item"] and first["event"]
    assert counts(ib)["spend_ledger"] == 0 and counts(ib)["memory_item"] == 0

    # The second owner streams: the fact, then a question that reaches the model.
    tell(b, f"Remember that {BIKE}")
    after_fact = counts(ia)
    assert after_fact == first, {k: (first[k], after_fact[k]) for k in first if first[k] != after_fact[k]}
    tell(b, ASK_B)

    # The first owner's data is exactly as it was.
    assert counts(ia) == first
    assert [e.id for e in rows_of(SpendEntry, agent_id=ia)] == spend_a
    # The second owner's turn landed in the second owner's instance only.
    assert_turn_stayed_in(tape, ib, ia, BIKE, LOCKER, ASK_B)
    now_b = counts(ib)
    assert now_b["memory_item"] == 1 and now_b["spend_ledger"] >= 1 and now_b["event"] >= 4
    assert {e.agent_id for e in rows_of(SpendEntry) if e.id not in spend_a} == {ib}
    assert {p.agent_id for p in rows_of(Person, display_name=OTHER["name"])} == {ib}
    # An agent was built for the second owner under the second owner's scope (and none under a wrong one).
    assert tape.built.count(ib) == 1 and tape.built.count(ia) == 1 and len(tape.built) == 2
    # The signed-in views agree: each notebook has only its owner's note.
    assert "zq9032" in b.get("/app/notebook").text and "zq4417" not in b.get("/app/notebook").text
    assert "zq4417" in a.get("/app/notebook").text and "zq9032" not in a.get("/app/notebook").text


def test_the_second_owner_can_go_first(browser, many, tape):
    """The same, with the second owner streaming before the first has sent anything: the first owner has
    no chat history or ledger yet, so a turn landing in the default instance would show at once."""
    a, b = two_owners(browser)
    ia, ib = instance_of(OWNER["email"]).id, instance_of(OTHER["email"]).id
    before, calls_before = counts(ia), len(tape.calls)
    tell(b, f"Remember that {BIKE}")
    tell(b, ASK_B)
    assert counts(ia) == before
    assert ib in tape.built
    assert len(tape.calls) > calls_before and {c[0] for c in tape.calls[calls_before:]} == {ib}
    assert {e.agent_id for e in rows_of(SpendEntry)} == {ib}
    assert {m.agent_id for m in rows_of(MemoryItem)} == {ib}
    assert {e.agent_id for e in rows_of(Event) if e.content == ASK_B} == {ib}
    tell(a, ASK_A)
    assert {c[0] for c in tape.calls if ASK_A in c[2]} == {ia}
    assert counts(ib)["memory_item"] == 1


def test_two_owners_streaming_at_the_same_moment_do_not_mix(browser, many, tape):
    a, b = two_owners(browser)
    ia, ib = instance_of(OWNER["email"]).id, instance_of(OTHER["email"]).id
    tell(a, f"Remember that {LOCKER}")
    tell(b, f"Remember that {BIKE}")
    base_a, base_b = counts(ia), counts(ib)
    spend_before = {e.id for e in rows_of(SpendEntry)}

    # Both questions are inside the model at once (each waits for the other), several rounds over.
    errors: list[BaseException] = []
    for _ in range(3):
        meet = tape.meet = threading.Barrier(2)

        def go(client, text: str) -> None:
            try:
                tell(client, text)
            except BaseException as exc:  # noqa: BLE001 (reported below)
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(a, ASK_A)), threading.Thread(target=go, args=(b, ASK_B))]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        assert not meet.broken, "the two turns were never inside the model at the same moment"
    assert not errors, errors

    for scope, ask, secret, other_secret in ((ia, ASK_A, LOCKER, BIKE), (ib, ASK_B, BIKE, LOCKER)):
        asked = [c for c in tape.calls if ask in c[2]]
        assert len(asked) == 3 and {c[0] for c in asked} == {scope}
        assert all(other_secret not in c[1] for c in asked)
        assert all(secret in c[1] for c in asked)
        assert {e.agent_id for e in rows_of(Event) if e.content == ask} == {scope}
        assert len([e for e in rows_of(Event) if e.content == ask]) == 3
    new_spend = [e for e in rows_of(SpendEntry) if e.id not in spend_before]
    assert len(new_spend) == 6 and Counter(e.agent_id for e in new_spend) == Counter({ia: 3, ib: 3})
    # Nothing but the answers' own rows were added to either instance, and no fact was copied across.
    assert counts(ia)["memory_item"] == base_a["memory_item"] == 1
    assert counts(ib)["memory_item"] == base_b["memory_item"] == 1
    assert [m.content for m in rows_of(MemoryItem, agent_id=ia)] == [LOCKER]
    assert [m.content for m in rows_of(MemoryItem, agent_id=ib)] == [BIKE]
