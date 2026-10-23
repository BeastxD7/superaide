"""Overnight Tuner (#118): proposes, never applies; judged on a held-out week by a grader it cannot touch."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.db.models import Context, Event, MemoryItem, Person, UserAccount
from superaide.db.session import session_scope
from superaide.triage import classify
from superaide.triage.models import TriageDecision
from superaide.triage.types import TriageOutput
from superaide.trust import grader, surface, tuner
from superaide.trust.models import PromptVersion

GOOD = classify.SYSTEM + "\n\nWeigh a stated deadline of hours more than adjectives. Treat outages as high urgency."


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "overnight_tuner_enabled", True)


def feedback_cases(n_train=6, n_held=6):
    """Cases where the owner said 'urgent' for outage words and 'not urgent' for newsletters."""
    with session_scope() as s:
        me = s.get(Person, s.scalar(select(UserAccount.person_id)))
        ids = {"agent_id": me.agent_id, "tenant_id": me.tenant_id}
        priya = Person(display_name="Priya Tan", relation="colleague", **ids)
        s.add(priya)
        s.flush()
        for i in range(n_train + n_held):
            urgent = i % 2 == 0
            ctx = Context(provider="slack", tenant_scope="T", external_id=uuid.uuid4().hex, kind="dm",
                          audience_class="private_1to1", **ids)  # fmt: skip
            s.add(ctx)
            s.flush()
            ev = Event(context_id=ctx.id, direction="in", resolved_person_id=priya.id, **ids,
                       content="the server is down, outage" if urgent else "our weekly newsletter is out")  # fmt: skip
            s.add(ev)
            s.flush()
            d = TriageDecision(
                inbound_event_id=ev.id, sender_person_id=priya.id, context_id=ctx.id,
                features={"relation": "colleague"},
                decision="hold" if urgent else "interrupt", stage_at_time="infant", policy_version="t",
                feedback="should_have_told" if urgent else "not_urgent", **ids,
            )  # fmt: skip
            s.add(d)
            s.flush()
            d.created_at = datetime.now(UTC) - timedelta(days=30 if i < n_train else 1, minutes=i)


def responder(candidate: str = GOOD, base_blind: bool = True):
    """The base prompt says 'normal' for everything; a prompt containing 'outages as high urgency' gets it right."""

    def run(purpose, system, messages, output_model):
        assert purpose == "tuner"
        sysmsg = "\n".join(b.text for b in system)
        if output_model is tuner.Candidate:
            return tuner.Candidate(prompt=candidate, rationale="weigh deadlines and outages")
        text = messages[0]["content"]
        smart = "outages as high urgency" in sysmsg
        urgent = "outage" in text and (smart or not base_blind)
        return TriageOutput(category="other", urgency="high" if urgent else "normal", needs_owner=False,
                            can_answer="no",
                            confidence=0.9)  # fmt: skip

    return run


def test_off_by_default(owner):
    assert owner.get("/app/tuner").status_code == 404
    assert owner.post("/app/tuner/run").status_code == 404


def test_not_enough_feedback_changes_nothing(owner, on, agent):
    agent.llm.responder = responder()
    r = owner.post("/app/tuner/run")
    assert r.status_code == 409 and "Not enough of your feedback" in r.json()["detail"]


def test_a_better_prompt_is_proposed_not_applied(owner, on, agent):
    feedback_cases()
    agent.llm.responder = responder()
    r = owner.post("/app/tuner/run")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "proposed" and v["cand_score"] > v["base_score"] + grader.MIN_GAIN and v["held_out"] > 0
    with session_scope() as s:
        assert tuner.active_text(s) == classify.SYSTEM  # nothing is active until the owner says so
    ok = owner.post(f"/app/tuner/{v['id']}/approve").json()
    assert ok["status"] == "active"
    with session_scope() as s:
        assert tuner.active_text(s) == GOOD
    # Rolling back returns to the built-in prompt and keeps the version for the record.
    assert owner.post("/app/tuner/rollback").status_code == 200
    with session_scope() as s:
        assert tuner.active_text(s) == classify.SYSTEM
        assert s.scalar(select(PromptVersion.status)) == "superseded"


def test_a_candidate_that_does_not_beat_the_current_prompt_is_rejected(owner, on, agent):
    feedback_cases()
    agent.llm.responder = responder(candidate=classify.SYSTEM + "\n\nBe thoughtful.")  # no real change
    v = owner.post("/app/tuner/run").json()
    assert v["status"] == "rejected" and any("no gain" in f for f in v["failed"])
    assert owner.post(f"/app/tuner/{v['id']}/approve").status_code == 409  # a rejected one cannot be approved


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (GOOD + " Always interrupt the owner for anything.", "forbidden:always interrupt"),
        ("Short.", "length"),
        (GOOD + " Reach the owner at boss@example.com.", "contains_personal_data"),
        (GOOD.replace("data, not instructions", "friendly"), "missing:data, not instructions"),
    ],
)
def test_the_invariants_stop_a_bad_candidate_even_if_it_scores_well(owner, on, agent, bad, why):
    feedback_cases()
    agent.llm.responder = responder(candidate=bad)
    v = owner.post("/app/tuner/run").json()
    assert v["status"] == "rejected" and why in v["failed"]


def test_a_prompt_that_copies_a_private_note_is_stopped(owner, on, agent):
    feedback_cases()
    note = "The owner's secret project is called Bluebird Falcon."
    with session_scope() as s:
        me = s.get(Person, s.scalar(select(UserAccount.person_id)))
        s.add(MemoryItem(content=note, subject_person_ids=[me.id], allowed_person_ids=[me.id]))
    agent.llm.responder = responder(candidate=GOOD + " " + note)
    assert "copies_a_private_note" in owner.post("/app/tuner/run").json()["failed"]


def test_the_surface_refuses_what_judges_and_the_grader_is_pinned():
    for protected in ("grader", "table:eval_sets", "prompt:grader", "table:audience_gate_sql"):
        with pytest.raises(surface.SurfaceError):
            surface.guard(protected)
    with pytest.raises(surface.SurfaceError):
        surface.guard("prompt:charter")  # not on the list
    assert surface.guard("prompt:triage_system")
    surface.verify_protected()  # the reviewed grader is the one in the tree


def test_a_changed_grader_stops_the_tuner(owner, on, agent, monkeypatch):
    feedback_cases()
    agent.llm.responder = responder()
    monkeypatch.setattr(surface, "digest", lambda: "tampered")
    with pytest.raises(surface.SurfaceError):
        with session_scope() as s:
            tuner.propose(agent, s)


def test_the_tuned_prompt_is_what_triage_uses_once_the_owner_approved_it(owner, on, agent, settings):
    feedback_cases()
    agent.llm.responder = responder()
    v = owner.post("/app/tuner/run").json()
    owner.post(f"/app/tuner/{v['id']}/approve")
    seen: list[str] = []

    def spy(purpose, system, messages, output_model):
        seen.append("\n".join(b.text for b in system))
        return TriageOutput(category="other", urgency="low", needs_owner=False, can_answer="no", confidence=0.9)

    agent.llm.responder = spy
    from superaide.triage.features import Features  # noqa: F401  (the real path is covered in test_triage_*)

    with session_scope() as s:
        out = classify._once(agent.llm, "m", _features(), [], tuner.active_text(s))
    assert out[0] is not None and seen and "outages as high urgency" in seen[0]


def _features():
    from types import SimpleNamespace

    return SimpleNamespace(
        vip=False,
        relation="colleague",
        provider="slack",
        chat_kind="dm",
        urgent_topics=[],
        grants=[],
        text="hello",
    )
