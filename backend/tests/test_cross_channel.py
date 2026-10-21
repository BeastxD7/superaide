"""One Ava across channels: what the owner says on Telegram, Ava knows in the app chat.

Reproduces the bug from 2026-10-21 (dev DB): the owner said "btw i dont like black clour at
all!" on Telegram, a fact "Shashank does not like the colour black." was learned, and then in
the app chat "do i like black colour?" got "I don't have anything about that". The model's
recall query was "black colour favourite color preference", which `websearch_to_tsquery`
turns into an AND of all five words, so the fact never matched.

The passing tests pin what already works (same person, same audience, learning on Telegram).
The xfail tests are the gaps ADR 0008 closes; each flips to a pass when its work package lands
(strict, so a fix that lands silently is noticed).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app import accounts
from superaide.app.accounts import owner as hired_owner
from superaide.app.accounts import web_message
from superaide.brain.llm import FakeLLM
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.context.audience import audience_for
from superaide.context.recall import recall
from superaide.db.models import MemoryItem
from superaide.db.session import session_scope
from superaide.identity.resolver import resolve
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.facts import Fact, FactList
from superaide.knowledge.learn import worth_reading
from superaide.knowledge.profile import owner_brief
from superaide.main import create_app
from superaide.memory.store import get_or_create_context
from tests.conftest import tg

load()
PASSWORD = "correct horse battery"
PHONE = "777001"
SAID_ON_TELEGRAM = "btw i dont like black colour at all!"
LEARNED = "Shashank does not like the colour black."
# The query Haiku actually sent in the dev DB, and paraphrases a person would use.
MODEL_QUERY = "black colour favourite color preference"
PARAPHRASES = [MODEL_QUERY, "black color", "what colours do I dislike", "colors I hate"]


class Recorder:
    """A fake Claude: scripted facts for learning, a plain reply for chat; records chat prompts."""

    def __init__(self) -> None:
        self.chat_messages: list[list[dict[str, Any]]] = []
        self.chat_systems: list[str] = []

    def __call__(self, purpose, system, messages, output_model):
        if purpose == "intro_facts":
            return FactList(facts=[Fact(content=LEARNED, category="preference", confidence=1)])
        self.chat_messages.append(messages)
        self.chat_systems.append("\n".join(b.text for b in system))
        if output_model is not None:  # the structured path groups and other people use
            return TurnOutput(
                reply="Noted.",
                requested_action=RequestedAction(kind="none", summary=""),
                prediction=Prediction(expectation="they reply", confidence=0.5),
            )
        return "Noted."


@pytest.fixture
def scenario(agent, monkeypatch):
    """The owner signs up, links Telegram, says the colour thing there, and learning runs."""
    rec = Recorder()
    monkeypatch.setattr("superaide.factory.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(rec))
    agent.llm = FakeLLM(rec)
    agent.settings.web_tools_enabled = False

    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        code = accounts.create_invite(s, person, "telegram")
    assert "Linked" in (agent.handle(tg(PHONE, f"/start {code}")).reply or "")

    assert agent.handle(tg(PHONE, SAID_ON_TELEGRAM)).mode == "known"
    run_due()  # learn_from_turn
    return client, rec


def _web_audience(s):
    person = hired_owner(s)
    msg = web_message(person, "do i like black colour?")
    sender = resolve(s, msg)
    return person, sender, audience_for(get_or_create_context(s, msg), sender)


# --- what already works (not the cause) ------------------------------------------------


def test_telegram_turns_are_learned_into_an_owner_only_fact(scenario):
    with session_scope() as s:
        person = hired_owner(s)
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == LEARNED))
        assert fact is not None
        assert fact.allowed_kind == "persons" and fact.allowed_person_ids == [person.id] and fact.min_trust == 2


def test_the_web_chat_audience_can_see_the_fact_learned_on_telegram(scenario):
    """Same Person, same {owner} audience, trust LINKED on both: the gate is not the problem."""
    with session_scope() as s:
        _, sender, aud = _web_audience(s)
        assert aud.person_ids == {sender.person.id} and not aud.has_unknown and aud.min_trust == 2
        # An exact keyword query finds it, so the fact is reachable from the web chat.
        assert any(src.text == LEARNED for src in recall(s, aud, "black colour"))


def test_nothing_from_the_owners_dm_reaches_a_group_they_are_in(scenario, agent):
    """Guard for WP1: the unified timeline is for the owner's private DMs only."""
    _, rec = scenario
    rec.chat_messages.clear()
    rec.chat_systems.clear()
    agent.handle(tg(PHONE, "what colour do I dislike?", chat_kind="group", chat_id="-100500"))
    assert rec.chat_messages  # the group turn did reach the model
    prompt = str(rec.chat_messages) + "\n".join(rec.chat_systems)
    assert SAID_ON_TELEGRAM not in prompt and LEARNED not in prompt


# --- the gaps (ADR 0008) ---------------------------------------------------------------


@pytest.mark.parametrize("query", PARAPHRASES)
def test_recall_finds_the_fact_for_a_paraphrase(scenario, query):
    with session_scope() as s:
        _, _, aud = _web_audience(s)
        assert any(src.text == LEARNED for src in recall(s, aud, query))


def test_the_app_chat_prompt_includes_what_the_owner_just_said_on_telegram(scenario):
    client, rec = scenario
    rec.chat_messages.clear()
    assert client.post("/app/chat", json={"text": "do i like black colour?"}).status_code == 200
    prompt = str(rec.chat_messages[0])
    assert SAID_ON_TELEGRAM in prompt and "Telegram" in prompt


def test_a_learned_preference_is_pinned_into_the_owner_brief(scenario):
    with session_scope() as s:
        _, sender, aud = _web_audience(s)
        assert "colour black" in owner_brief(s, sender, aud)


def test_the_owner_turn_tells_ava_that_what_the_owner_says_is_kept(scenario):
    client, rec = scenario
    rec.chat_systems.clear()
    client.post("/app/chat", json={"text": "hello"})
    assert "learn from what your owner tells you" in rec.chat_systems[0]


@pytest.mark.parametrize("text", ["I hate black.", "no beige, ever", "I'm vegetarian"])
def test_a_short_statement_of_preference_is_worth_learning(text):
    assert worth_reading(text)


class Reader(Recorder):
    """The extractor: answers with `fact`, pointing at the known fact that contains `points_at`."""

    def __init__(self, fact: str, relation: str, points_at: str = "colour black") -> None:
        super().__init__()
        self.fact, self.relation, self.points_at, self.known = fact, relation, points_at, ""

    def __call__(self, purpose, system, messages, output_model):
        if purpose != "intro_facts":
            return "Noted."
        self.known = messages[0]["content"]
        line = next((ln for ln in self.known.splitlines() if self.points_at in ln and ":" in ln[:3]), "")
        ref = int(line.split(":", 1)[0]) if line else None
        return FactList(facts=[Fact(content=self.fact, category="preference", confidence=1, **{self.relation: ref})])


def _live_preferences():
    with session_scope() as s:
        rows = s.scalars(select(MemoryItem).where(MemoryItem.category == "preference", MemoryItem.valid_to.is_(None)))
        return [(m.content, len(m.source_event_ids)) for m in rows]


def test_the_same_preference_said_again_in_other_words_is_kept_once(scenario, monkeypatch):
    reader = Reader("Shashank dislikes the color black.", "same_as")
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(reader))
    client, _ = scenario
    client.post("/app/chat", json={"text": "I really can't stand black as a colour, honestly"})
    run_due()
    assert f"1: {LEARNED}" in reader.known  # the extractor was shown what Ava already knew
    assert _live_preferences() == [(LEARNED, 2)]  # one fact, learned from both messages


def test_the_same_fact_in_other_spelling_is_kept_once_even_unflagged(scenario, monkeypatch):
    reader = Reader("Shashank does not like the color black", "same_as", points_at="nothing matches this")
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(reader))
    client, _ = scenario
    client.post("/app/chat", json={"text": "Just so you know: I do not like black, the color, at all"})
    run_due()
    assert _live_preferences() == [(LEARNED, 2)]


def test_changing_your_mind_retires_the_old_fact(scenario, monkeypatch):
    reader = Reader("Shashank likes the colour black now.", "replaces")
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(reader))
    client, _ = scenario
    client.post("/app/chat", json={"text": "Actually I've come round to black, I like it now"})
    run_due()
    assert _live_preferences() == [("Shashank likes the colour black now.", 1)]
    with session_scope() as s:
        old = s.scalar(select(MemoryItem).where(MemoryItem.content == LEARNED))
        assert old is not None and old.valid_to is not None  # kept for history, no longer current


def test_a_fact_never_merges_into_one_with_a_different_audience(scenario, monkeypatch):
    with session_scope() as s:
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == LEARNED))
        fact.allowed_kind, fact.allowed_person_ids, fact.min_trust = "public", [], 0  # shared on purpose
    reader = Reader("Shashank dislikes the color black.", "same_as")
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(reader))
    client, _ = scenario
    client.post("/app/chat", json={"text": "I really can't stand black as a colour, honestly"})
    run_due()
    with session_scope() as s:
        shared = s.scalar(select(MemoryItem).where(MemoryItem.content == LEARNED))
        assert len(shared.source_event_ids) == 1  # the public note didn't absorb a private message
        private = s.scalar(select(MemoryItem).where(MemoryItem.content == "Shashank dislikes the color black."))
        assert private is not None and private.allowed_person_ids != []


def test_a_matching_note_is_attached_to_the_message_before_the_model_runs(scenario):
    client, rec = scenario
    rec.chat_messages.clear()
    client.post("/app/chat", json={"text": "what colors do I hate?"})
    first = rec.chat_messages[0][-1]["content"]
    blocks = [b for b in first if b.get("type") == "search_result"]
    assert [b["content"][0]["text"] for b in blocks] == [LEARNED]
    assert blocks[0]["title"] == "Note · preference · from Telegram"
    assert first[-1] == {"type": "text", "text": "what colors do I hate?"}


def test_offline_ava_makes_no_claim_about_learning(agent, model):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    client.post("/app/chat", json={"text": "hello"})
    assert model.systems and "learn from what your owner tells you" not in model.systems[-1]


@pytest.mark.parametrize("text", ["ok", "thanks", "sounds good", "I'm fine thanks", "never mind", "what's up?"])
def test_small_talk_is_still_not_worth_learning(text):
    assert not worth_reading(text)
