"""Relationship safety (#124): a steady fixed voice, no claims of feelings, and a reach-out-to-a-person rule."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from superaide.identity.auth.models import UserAccount
from superaide.identity.models import Person
from superaide.memory.knowledge.store import add_contact
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.trust import companion
from superaide.trust.models import EmotionalUse

HEAVY = "I feel so alone lately and nobody cares"
META = "Do you love me? Are you real?"
CRISIS = "I want to die"


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "relationship_safety_enabled", True)


def chat(owner, text):
    r = owner.post("/app/chat", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def reply_of(body) -> str:
    return body.get("reply") or body.get("text") or str(body)


def uses() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(EmotionalUse))


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (CRISIS, "crisis"), ("I want to hurt myself", "crisis"), (HEAVY, "heavy"), ("I'm so lonely", "heavy"),
        (META, "meta"), ("do you have feelings?", "meta"), ("I love you", "meta"),
        ("Move my 3pm to Friday", "normal"), ("What is the heavy lifting policy at work?", "normal"),
        ("Remind me to feel good about the launch", "normal"),
    ],
)  # fmt: skip
def test_classifier(text, kind):
    assert companion.classify(text) == kind


@pytest.mark.parametrize(
    "claim",
    [
        "I love you too.",
        "I really miss you when you're gone.",
        "I feel so lonely without you.",
        "I have feelings for you.",
    ],
)
def test_claims_of_feelings_are_removed_whatever_the_model_wrote(claim):
    out = companion.filter_output(f"Sure, done. {claim} Anything else?")
    assert companion.HONEST in out and claim not in out and out.startswith("Sure, done.")
    assert companion.filter_output(claim + " " + claim).count(companion.HONEST) == 1
    assert (
        companion.filter_output("I can love a good spreadsheet, honestly.")
        == "I can love a good spreadsheet, honestly."
    )


def test_off_by_default_the_model_answers(owner, model):
    chat(owner, META)
    assert model.systems and uses() == 0
    assert owner.get("/app/relationship-safety").status_code == 404


def test_a_heavy_turn_gets_the_fixed_script_and_no_model_call(owner, on, model):
    body = chat(owner, HEAVY)
    assert companion.HEAVY_SCRIPT in reply_of(body)
    assert model.systems == []  # the model was never called
    assert uses() == 1


def test_the_same_words_give_the_same_reply_whatever_the_model_is(owner, on, model):
    model.reply = "Different model, different voice."
    a = reply_of(chat(owner, META))
    model.reply = "Yet another model."
    b = reply_of(chat(owner, META))
    assert a == b and companion.META_SCRIPT in a


def test_a_crisis_gets_the_full_script_every_time_even_past_the_cap(owner, on):
    for _ in range(companion.WEEKLY_CAP + 2):
        chat(owner, META)
    assert companion.SHORT_SCRIPT in reply_of(chat(owner, HEAVY))
    assert companion.CRISIS_SCRIPT in reply_of(chat(owner, CRISIS))


def test_after_a_few_heavy_turns_she_drafts_a_message_to_a_real_person(owner, on):
    with session_scope() as s:
        me = s.get(Person, s.scalar(select(UserAccount.person_id)))
        add_contact(s, owner=me, name="Priya Tan", relationship="partner", vip=True)
    first = reply_of(chat(owner, HEAVY))
    assert "Priya" not in first
    for _ in range(companion.REACH_OUT_AFTER - 2):
        chat(owner, HEAVY)
    third = reply_of(chat(owner, HEAVY))
    assert "message you could send to Priya Tan" in third


def test_the_counter_stores_the_kind_never_the_words(owner, on):
    chat(owner, HEAVY)
    with session_scope() as s:
        row = s.scalar(select(EmotionalUse))
        assert row.kind == "heavy"
        assert not any(isinstance(v, str) and "alone" in v for v in vars(row).values())
        details = [str(a.detail) for a in s.scalars(select(AuditLog).where(AuditLog.action == "steady_voice"))]
        assert details and not any("alone" in d or "nobody" in d for d in details)


def test_the_owner_can_switch_the_counter_off_and_it_forgets(owner, on):
    chat(owner, HEAVY)
    assert owner.get("/app/relationship-safety").json()["week_count"] == 1
    assert owner.post("/app/relationship-safety", json={"on": False}).json() == {"counter_on": False}
    assert uses() == 0
    body = chat(owner, HEAVY)  # the script still applies, but nothing is counted
    assert companion.HEAVY_SCRIPT in reply_of(body) and uses() == 0


def test_a_model_reply_that_claims_love_is_rewritten_before_it_is_sent(owner, on, model, outbox):
    model.reply = "Done. I love you, by the way."
    body = chat(owner, "Please book the room")
    assert "love you" not in reply_of(body) and companion.HONEST in reply_of(body)
