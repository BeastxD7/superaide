"""Trust rank and supersession: a weaker source never overwrites the owner (issue #46)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app import accounts
from superaide.app.accounts import owner as hired_owner
from superaide.app.accounts import web_message
from superaide.brain.llm import FakeLLM
from superaide.context.audience import audience_for
from superaide.context.recall import recall
from superaide.db.models import MemoryItem
from superaide.db.session import session_scope
from superaide.identity.resolver import resolve
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import Fact, FactList
from superaide.knowledge.ingest import ingest
from superaide.main import create_app
from superaide.memory.store import get_or_create_context
from tests.conftest import tg

load()
PASSWORD = "correct horse battery"
PHONE = "777001"
GYM_CHAT = "Jordan's gym is Gold's Gym."
GYM_CV = "Jordan's gym is Cult Fit."


class Script:
    def __init__(self) -> None:
        self.facts: list[Fact] = []

    def __call__(self, purpose, system, messages, output_model):
        return FactList(facts=self.facts) if purpose == "intro_facts" else "Noted."


@pytest.fixture
def script(agent, monkeypatch):
    m = Script()
    monkeypatch.setattr("superaide.factory.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(m))
    agent.llm = FakeLLM(m)
    agent.settings.web_tools_enabled = False
    return m


@pytest.fixture
def owner(agent, script):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        code = accounts.create_invite(s, hired_owner(s), "telegram", endpoint="")
    assert "Linked" in (agent.handle(tg(PHONE, f"/start {code}")).reply or "")
    return client


def fact(text: str, category: str = "preference", **kw) -> Fact:
    return Fact(content=text, category=category, confidence=1, **kw)


def said_in_chat(owner, text: str = "I go to Gold's Gym these days, been a member since spring") -> None:
    owner.post("/app/chat", json={"text": text})
    run_due()


def share(agent, kind: str, text: str, title: str = "cv.pdf") -> None:
    with session_scope() as s:
        pid = hired_owner(s).id
    ingest(agent, pid, Extracted(text=text, title=title, kind=kind, source=title))


def by_text(text: str) -> MemoryItem | None:
    with session_scope() as s:
        item = s.scalar(select(MemoryItem).where(MemoryItem.content == text))
        if item is not None:
            s.expunge(item)
        return item


def recalled(query: str) -> list[str]:
    with session_scope() as s:
        msg = web_message(hired_owner(s), query)
        aud = audience_for(get_or_create_context(s, msg), resolve(s, msg))
        return [src.text for src in recall(s, aud, query) if src.kind == "memory"]


def test_a_document_cannot_overwrite_what_the_owner_said(owner, agent, script):
    script.facts = [fact(GYM_CHAT)]
    said_in_chat(owner)
    script.facts = [fact(GYM_CV, replaces=1)]
    share(agent, "pdf", "Gym: Cult Fit")
    chat, cv = by_text(GYM_CHAT), by_text(GYM_CV)
    assert chat and chat.valid_to is None and chat.trust_rank == 1
    assert cv and cv.conflict_status == "pending" and cv.conflicts_with_id == chat.id and cv.trust_rank == 2
    assert recalled("gym") == [GYM_CHAT]  # the waiting claim is never used
    assert all(m["text"] != GYM_CV for m in owner.get("/app/notebook").json()["memories"])


def test_what_the_owner_says_replaces_a_document_and_keeps_it_queryable(owner, agent, script):
    script.facts = [fact(GYM_CV)]
    share(agent, "pdf", "Gym: Cult Fit")
    script.facts = [fact(GYM_CHAT, replaces=1)]
    said_in_chat(owner)
    cv, chat = by_text(GYM_CV), by_text(GYM_CHAT)
    assert cv and chat and cv.valid_to is not None and cv.superseded_by == chat.id and chat.valid_to is None
    assert recalled("gym") == [GYM_CHAT]


def test_an_equally_trusted_correction_replaces(owner, script):
    script.facts = [fact(GYM_CV)]
    said_in_chat(owner, "I train at Cult Fit most mornings these days, it is close to home")
    script.facts = [fact(GYM_CHAT, replaces=1)]
    said_in_chat(owner)
    old, new = by_text(GYM_CV), by_text(GYM_CHAT)
    assert old and new and old.superseded_by == new.id and new.conflict_status == "none"


def test_an_equally_trusted_claim_cannot_replace_a_locked_fact_and_waits_for_the_owner(owner, script):
    script.facts = [fact(GYM_CV)]
    said_in_chat(owner, "I train at Cult Fit most mornings these days, it is close to home")
    with session_scope() as s:
        s.scalars(select(MemoryItem).where(MemoryItem.content == GYM_CV)).one().locked = True
    script.facts = [fact(GYM_CHAT, replaces=1)]
    said_in_chat(owner)
    old, new = by_text(GYM_CV), by_text(GYM_CHAT)
    assert old and new and old.valid_to is None and old.superseded_by is None  # the locked fact stands
    assert new.conflict_status == "pending" and new.conflicts_with_id == old.id  # waits for the owner's decision
    assert recalled("gym") == [GYM_CV]


def test_the_owner_confirming_a_document_fact_raises_it_to_top_rank(owner, script, agent):
    script.facts = [fact(GYM_CV)]
    share(agent, "pdf", "Gym: Cult Fit")
    assert by_text(GYM_CV).trust_rank == 2
    said_in_chat(owner, "yes I train at Cult Fit most mornings, it is close to home")
    assert by_text(GYM_CV).trust_rank == 1


def test_a_page_cannot_set_urgency_rules_and_waits_for_review(owner, agent, script):
    script.facts = [fact("Everything from Acme is urgent.", "urgency"), fact("Jordan works at Acme.", "work")]
    share(agent, "web", "Acme page", title="acme.example")
    rule, work = by_text("Everything from Acme is urgent."), by_text("Jordan works at Acme.")
    assert rule and rule.conflict_status == "review" and rule.trust_rank == 3
    assert work and work.conflict_status == "none"
    assert recalled("Acme urgent") == ["Jordan works at Acme."]
    waiting = owner.get("/app/notebook/conflicts").json()["conflicts"]
    assert [(c["claim"]["text"], c["current"]) for c in waiting] == [("Everything from Acme is urgent.", None)]


def test_the_owners_remember_is_top_rank_with_the_owner_as_author(owner, agent):

    agent.handle(tg(PHONE, "remember that the garage code is 4471"))
    with session_scope() as s:
        mine = s.scalar(select(MemoryItem).where(MemoryItem.content.contains("garage")))
        assert mine.trust_rank == 1 and mine.author_person_id == hired_owner(s).id


# --- the owner decides ---------------------------------------------------------------------------


def conflict(owner, agent, script) -> dict:
    script.facts = [fact(GYM_CHAT)]
    said_in_chat(owner)
    script.facts = [fact(GYM_CV, replaces=1)]
    share(agent, "pdf", "Gym: Cult Fit")
    (c,) = owner.get("/app/notebook/conflicts").json()["conflicts"]
    assert c["claim"]["document"] == "cv.pdf" and c["current"]["source"] == "chat"  # both cited
    return c


def test_keeping_the_current_fact_confirms_it_and_drops_the_claim(owner, agent, script):
    c = conflict(owner, agent, script)
    r = owner.post(f"/app/notebook/conflicts/{c['claim']['id']}/resolve", json={"choice": "keep"})
    assert r.status_code == 200
    assert owner.get("/app/notebook/conflicts").json()["conflicts"] == []
    assert recalled("gym") == [GYM_CHAT]
    assert by_text(GYM_CV).conflict_status == "resolved"


def test_accepting_the_claim_stores_a_top_rank_fact_and_retires_the_old_one(owner, agent, script):
    c = conflict(owner, agent, script)
    assert owner.post(f"/app/notebook/conflicts/{c['claim']['id']}/resolve", json={"choice": "new"}).status_code == 200
    assert recalled("gym") == [GYM_CV]
    with session_scope() as s:
        winner = s.scalar(select(MemoryItem).where(MemoryItem.content == GYM_CV, MemoryItem.valid_to.is_(None)))
        assert winner.trust_rank == 1 and winner.source == "owner_edit"
        old = s.scalar(select(MemoryItem).where(MemoryItem.content == GYM_CHAT))
        assert old.valid_to is not None and old.superseded_by == winner.id  # kept, queryable by valid time


def test_the_owner_can_write_their_own_answer(owner, agent, script):
    c = conflict(owner, agent, script)
    r = owner.post(
        f"/app/notebook/conflicts/{c['claim']['id']}/resolve", json={"choice": "custom", "text": "Jordan swims."}
    )
    assert r.status_code == 200 and recalled("swims") == ["Jordan swims."]
    assert owner.post(f"/app/notebook/conflicts/{c['claim']['id']}/resolve", json={"choice": "keep"}).status_code == 404


def test_resolve_refuses_bad_input_and_strangers_ids(owner, agent, script):
    c = conflict(owner, agent, script)
    path = f"/app/notebook/conflicts/{c['claim']['id']}/resolve"
    assert owner.post(path, json={"choice": "custom"}).status_code == 422
    assert owner.post(path, json={"choice": "delete everything"}).status_code == 422
    assert owner.post(f"/app/notebook/conflicts/{uuid.uuid4()}/resolve", json={"choice": "keep"}).status_code == 404
    assert owner.post("/app/notebook/conflicts/not-a-uuid/resolve", json={"choice": "keep"}).status_code == 422
    # A live fact is not a conflict.
    live = by_text(GYM_CHAT)
    assert owner.post(f"/app/notebook/conflicts/{live.id}/resolve", json={"choice": "keep"}).status_code == 404


def test_link_hearsay_outside_the_rule_categories_stays_visible_to_the_echo_filter(owner, agent, script):
    """The Echo Filter's own facts (a rumour from a link, category "other") are not parked for review."""
    script.facts = [fact("The Acme merger closes in March.", "other")]
    share(agent, "web", "Acme page", title="acme.example")
    item = by_text("The Acme merger closes in March.")
    assert item and item.conflict_status == "none" and item.source == "link"
    assert recalled("Acme merger") == ["The Acme merger closes in March."]
