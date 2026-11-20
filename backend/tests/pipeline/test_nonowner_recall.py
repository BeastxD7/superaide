"""A known person who is not the owner gets notes chosen for their question, not the newest five (#649)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from superaide.memory.audience import Audience
from superaide.memory.models import MemoryItem
from superaide.memory.recall.retrieval import memories_for_question
from superaide.platform.db.session import session_scope
from tests.conftest import COLLEAGUE_SLACK, sl

RETURN_NOTE = "Jordan is back from Lisbon on 4 March"
OWNER_CANARY = "CANARY-OWNERONLY-5d2e Lisbon hotel booking reference"


def _plant(owner_id) -> None:
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(MemoryItem(content=RETURN_NOTE, allowed_kind="org", min_trust=2, created_at=now - timedelta(days=30)))
        for i in range(14):  # newer org-visible notes about unrelated things
            s.add(
                MemoryItem(
                    content=f"Standup room number {i} is booked", allowed_kind="org", min_trust=2,
                    created_at=now - timedelta(days=20 - i),
                )
            )  # fmt: skip
        s.add(
            MemoryItem(
                content=OWNER_CANARY, allowed_kind="persons", allowed_person_ids=[owner_id], min_trust=2, created_at=now
            )
        )


def _aud(person) -> Audience:
    return Audience({person.id}, has_unknown=False, min_trust=2, all_internal=True)


def test_the_answer_is_found_though_it_is_not_among_the_newest(manager, colleague):
    _plant(manager.id)
    with session_scope() as s:
        found = [m.content for m in memories_for_question(s, _aud(colleague), "when is Jordan back from Lisbon?")]
    assert RETURN_NOTE in found
    assert len(found) <= 5
    assert OWNER_CANARY not in found


def test_a_question_with_no_match_falls_back_to_the_newest_allowed(manager, colleague):
    _plant(manager.id)
    with session_scope() as s:
        found = [m.content for m in memories_for_question(s, _aud(colleague), "zzqxv", limit=5)]
    assert len(found) == 5
    assert OWNER_CANARY not in found


def test_the_limit_holds_and_owner_notes_never_enter(manager, colleague):
    _plant(manager.id)
    with session_scope() as s:
        found = memories_for_question(s, _aud(colleague), "Lisbon hotel booking reference", limit=3)
    assert len(found) <= 3
    assert all(OWNER_CANARY not in m.content for m in found)


def test_unknown_audience_gets_public_notes_only(manager):
    _plant(manager.id)
    with session_scope() as s:
        s.add(MemoryItem(content="Office opens at nine", allowed_kind="public", min_trust=0))
        unknown = Audience({manager.id}, has_unknown=True, min_trust=0, all_internal=False)
        found = [m.content for m in memories_for_question(s, unknown, "when is Jordan back from Lisbon?")]
    assert found == ["Office opens at nine"] or "Office opens at nine" in found
    assert RETURN_NOTE not in found and OWNER_CANARY not in found


def test_the_colleague_prompt_holds_the_answer_and_no_owner_text(agent, model, manager, colleague):
    _plant(manager.id)
    agent.handle(sl(COLLEAGUE_SLACK, "when is Jordan back from Lisbon?"))
    assert model.systems
    prompt = model.systems[-1]
    assert RETURN_NOTE in prompt
    assert "CANARY-OWNERONLY" not in prompt
    notes = prompt.split("Memories you may use here:")[1].strip().splitlines()
    assert 1 <= len(notes) <= 5
