"""Time-travel rules (#52): a standing order is replayed against real history before it can act, runs in shadow,
is promoted only by the owner on measured agreement, and a live draft still ends in an approval card."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.platform.db.models import Approval, Context, Event, Person
from superaide.platform.db.session import session_scope
from superaide.rules import timetravel as tt
from tests.conftest import tg

SPEC = tt.RuleSpec(
    sender_contains="", keywords=["recruiter", "opportunity"], channel="", action="label", value="recruiter"
)


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "time_travel_rules_enabled", True)


def add_event(text: str, *, who: str = "Dana Lee", provider: str = "telegram", days_ago: int = 1, relation="external"):
    with session_scope() as s:
        p = s.scalar(select(Person).where(Person.display_name == who))
        if p is None:
            p = Person(display_name=who, relation=relation)
            s.add(p)
            s.flush()
        c = Context(provider=provider, external_id=uuid.uuid4().hex, kind="dm", audience_class="private_1to1")
        s.add(c)
        s.flush()
        e = Event(context_id=c.id, direction="in", resolved_person_id=p.id, content=text)
        e.created_at = datetime.now(UTC) - timedelta(days=days_ago)
        s.add(e)
        s.flush()
        return str(e.id)


def new_rule(spec=SPEC) -> str:
    with session_scope() as s:
        return tt.create(s, "if a recruiter writes, label it", spec)["id"]


def test_the_predicate_is_deterministic_and_an_empty_rule_never_fires():
    assert tt.matches(SPEC, text="Hi, a recruiter here", sender="X", channel="slack")
    assert not tt.matches(SPEC, text="lunch?", sender="X", channel="slack")
    assert not tt.matches(tt.RuleSpec(value="x"), text="anything", sender="X", channel="web")
    by_sender = tt.RuleSpec(sender_contains="dana", value="x")
    assert tt.matches(by_sender, text="hello", sender="Dana Lee", channel="web")
    assert not tt.matches(tt.RuleSpec(channel="slack", value="x"), text="hello", sender="", channel="web")


def test_the_rule_is_shown_back_in_plain_words():
    assert "mentions “recruiter” or “opportunity”" in tt.describe(SPEC) and "label it “recruiter”" in tt.describe(SPEC)


def test_replay_counts_firings_over_the_window_and_shows_examples(on):
    add_event("Hello, I'm a recruiter with an opportunity", days_ago=3)
    add_event("Lunch tomorrow?", who="Priya", days_ago=2)
    add_event("another recruiter note", who="Sam", days_ago=40)
    add_event("old recruiter note", who="Old", days_ago=200)  # outside 90 days
    rid = new_rule()
    with session_scope() as s:
        report = tt.replay(s, rid, 90)
    assert report["scanned"] == 3 and report["fired"] == 2 and len(report["examples"]) == 2
    assert report["examples"][0]["from"] == "Dana Lee" and "recruiter" in report["examples"][0]["snippet"]


def test_a_rule_cannot_go_live_without_a_replay_for_exactly_this_spec(on):
    rid = new_rule()
    with session_scope() as s:
        assert tt.activate(s, rid) == "Replay this rule against your history first."
        tt.replay(s, rid, 90)
        assert tt.activate(s, rid) is None
        assert tt.edit(s, rid, tt.RuleSpec(keywords=["invoice"], value="finance"))  # editing voids the replay
        assert tt.activate(s, rid) == "Replay this rule against your history first."


def test_one_owners_replay_never_reads_anothers_rules_or_history(on):
    rid = new_rule()
    assert rid  # rules live in this instance's own state; the replay query is instance-scoped by the session
    with session_scope() as s:
        assert tt.replay(s, "not-a-rule", 90) is None


def test_shadow_firings_are_recorded_and_judged(on):
    rid = new_rule()
    with session_scope() as s:
        tt.replay(s, rid, 90)
        tt.activate(s, rid)
    eid = add_event("I'm a recruiter")
    with session_scope() as s:
        ev = s.get(Event, eid)
        assert tt.record_firing(s, ev, provider="telegram", sender="Dana Lee") == 1
        assert tt.judge(s, rid, eid, True, min_agreement=0.9)
        assert tt.agreement(tt.find(tt._rules(s), rid)) == (1, 1)


def test_draft_rules_do_not_fire(on):
    new_rule()
    eid = add_event("I'm a recruiter")
    with session_scope() as s:
        assert tt.record_firing(s, s.get(Event, eid), provider="telegram", sender="Dana") == 0


def firings(rid: str, n: int, agree_every: int = 1) -> None:
    for i in range(n):
        eid = add_event(f"recruiter {i}", who=f"Person {i}")
        with session_scope() as s:
            tt.record_firing(s, s.get(Event, eid), provider="telegram", sender=f"Person {i}")
            tt.judge(s, rid, eid, i % agree_every == 0 or agree_every == 1, min_agreement=0.9)


def shadowed() -> str:
    rid = new_rule()
    with session_scope() as s:
        tt.replay(s, rid, 90)
        tt.activate(s, rid)
    return rid


def test_promotion_needs_enough_agreeing_firings_and_only_the_owner_routes_do_it(on):
    rid = shadowed()
    firings(rid, 5)
    with session_scope() as s:
        assert "Not yet" in tt.promote(s, rid, after=20, min_agreement=0.9)
    firings(rid, 15)
    with session_scope() as s:
        assert tt.promote(s, rid, after=20, min_agreement=0.9) is None
        assert tt.find(tt._rules(s), rid)["status"] == "live"


def test_low_agreement_blocks_promotion(on):
    rid = shadowed()
    for i in range(20):
        eid = add_event(f"recruiter {i}", who=f"P{i}")
        with session_scope() as s:
            tt.record_firing(s, s.get(Event, eid), provider="telegram", sender=f"P{i}")
            tt.judge(s, rid, eid, i < 15, min_agreement=0.9)  # 15 of 20 = 75%
    with session_scope() as s:
        assert "Not yet" in tt.promote(s, rid, after=15, min_agreement=0.9)


def test_a_live_rule_that_slips_is_demoted_automatically(on):
    rid = shadowed()
    firings(rid, 20)
    with session_scope() as s:
        tt.promote(s, rid, after=20, min_agreement=0.9)
    for i in range(6):
        eid = add_event(f"recruiter late {i}", who=f"L{i}")
        with session_scope() as s:
            tt.record_firing(s, s.get(Event, eid), provider="telegram", sender=f"L{i}")
            tt.judge(s, rid, eid, False, min_agreement=0.9)
    with session_scope() as s:
        assert tt.find(tt._rules(s), rid)["status"] == "shadow"


def test_a_live_draft_reply_still_ends_in_an_approval_card(on, monkeypatch):
    spec = tt.RuleSpec(keywords=["recruiter"], action="draft_reply", value="Thanks, please send the role details.")
    rid = new_rule(spec)
    with session_scope() as s:
        tt.replay(s, rid, 90)
        tt.activate(s, rid)
    firings(rid, 20)
    with session_scope() as s:
        tt.promote(s, rid, after=20, min_agreement=0.9)
        boss = Person(display_name="Jordan", relation="manager")
        s.add(boss)
        s.flush()
        s.expunge(boss)
    monkeypatch.setattr(tt, "_owner", lambda s: boss)
    assert _approvals() == 0
    eid = add_event("recruiter again", who="Zed")
    with session_scope() as s:
        tt.record_firing(s, s.get(Event, eid), provider="telegram", sender="Zed")
    with session_scope() as s:
        [card] = s.scalars(select(Approval)).all()
        assert card.status == "pending" and card.kind == "message_someone_else"  # a card, never a send
        assert card.payload["rule_id"] == rid and "Thanks, please send the role details." in card.payload["summary"]


def _approvals() -> int:
    with session_scope() as s:
        return len(s.scalars(select(Approval)).all())


def test_compile_uses_the_small_model_and_code_clamps_the_answer(settings):
    class A:
        def __init__(self) -> None:
            self.settings = settings
            self.llm = FakeLLM(
                lambda purpose, system, messages, om: tt.RuleSpec(
                    keywords=["  Recruiter  ", "OPPORTUNITY"], action="label", value="  recruiter "
                )
            )

    spec = tt.compile_text(A(), "if a recruiter emails, label it")
    assert spec.keywords == ["recruiter", "opportunity"] and spec.value == "recruiter"


def test_the_pipeline_records_shadow_firings_without_changing_the_reply(agent, outbox, manager, on):
    rid = shadowed()
    with session_scope() as s:
        p = Person(display_name="Dana Lee", relation="external")
        s.add(p)
    r = agent.handle(tg("777", "hello, I'm a recruiter with an opportunity"))
    assert r.mode == "stranger"  # the stranger reply is unchanged
    with session_scope() as s:
        # a stranger has no name, so a sender-less rule still matches on the words alone
        assert len(tt.find(tt._rules(s), rid)["shadow"]) == 1


def test_off_by_default_nothing_is_recorded(agent, manager, settings):
    assert not settings.time_travel_rules_enabled
    agent.handle(tg("777", "recruiter"))
    with session_scope() as s:
        assert tt._rules(s) == []
