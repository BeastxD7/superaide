# ruff: noqa: F811  (the triage tests' fixtures are imported, then used by name)
"""#117: the deletable kNN triage head, and promotion on a Wilson lower bound.

The head is a shadow score: it never changes a decision, it reads only the owner's own button presses,
and taking a label back removes its influence at once.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from superaide.brain.embed import FakeEmbedder, NoEmbedder
from superaide.config import get_settings
from superaide.db.models import Context, Event, Person
from superaide.db.session import session_scope
from superaide.triage import escalate, knn, promotion
from superaide.triage.models import TriageDecision, TriageLabelVector
from superaide.triage.types import POLICY_VERSION, Stage
from tests.conftest import COLLEAGUE_SLACK, sl
from tests.triage.test_triage_escalation import decisions, drain, on, pushes, world  # noqa: F401  (fixtures)

URGENT = ["the server is down", "server down again, production is down", "database down right now", "api down"]
CALM = ["lunch menu for friday", "thanks for the lunch", "lunch on friday?", "friday lunch plans"]


def label(owner: Person, text: str, feedback: str | None, *, via: str | None = "app", **features) -> uuid.UUID:
    ids = {"agent_id": owner.agent_id, "tenant_id": owner.tenant_id}
    with session_scope() as s:
        who = Person(display_name="Priya Tan", relation="colleague", **ids)
        ctx = Context(provider="slack", tenant_scope="T", external_id=uuid.uuid4().hex, kind="dm",
                      audience_class="private_1to1", **ids)  # fmt: skip
        s.add_all([who, ctx])
        s.flush()
        ev = Event(context_id=ctx.id, direction="in", content=text, resolved_person_id=who.id, **ids)
        s.add(ev)
        s.flush()
        d = TriageDecision(inbound_event_id=ev.id, sender_person_id=who.id, context_id=ctx.id, features=features,
                           decision="interrupt", stage_at_time="infant", policy_version=POLICY_VERSION,
                           feedback=feedback, feedback_via=via if feedback else None, **ids)  # fmt: skip
        s.add(d)
        s.flush()
        return d.id


@pytest.fixture
def me(manager) -> Person:
    return manager


@pytest.fixture
def labelled(me) -> list[uuid.UUID]:
    ids = [label(me, t, "useful") for t in URGENT] + [label(me, t, "not_urgent") for t in CALM]
    return ids


def head(text: str, **kw):
    with session_scope() as s:
        return knn.knn_urgency(s, FakeEmbedder(), text, **kw)


# --- Wilson bound ---------------------------------------------------------------------------------


def test_wilson_lower_bound_punishes_small_samples():
    assert promotion.wilson_lower(0, 0) == 0.0
    nine_of_ten, ninety_of_hundred = promotion.wilson_lower(9, 10), promotion.wilson_lower(90, 100)
    assert nine_of_ten == pytest.approx(0.6523, abs=1e-3)
    assert ninety_of_hundred == pytest.approx(0.8396, abs=1e-3)
    assert nine_of_ten < ninety_of_hundred < 0.9  # the same 90% earns more with more evidence
    assert promotion.wilson_lower(10, 10) < 1.0
    assert promotion.wilson_lower(5, 10) <= promotion.wilson_lower(6, 10)


def test_promotion_needs_the_lower_bound_not_the_point_estimate():
    small = promotion.judge(Stage.INFANT, useful=9, not_urgent=1)  # 90% but only 10 events
    assert small.precision == 0.9 and not small.ready_to_offer and small.needed_events == 30
    big = promotion.judge(Stage.INFANT, useful=27, not_urgent=3)  # 90% over 30 events: lower bound ~0.77
    assert big.ready_to_offer
    thin = promotion.judge(Stage.INFANT, useful=19, not_urgent=11)  # 63% point estimate, bound below 0.6
    assert thin.precision == pytest.approx(0.6333, abs=1e-3) and not thin.ready_to_offer


def test_demotion_is_advised_when_the_bound_drops_below_the_gate_it_was_earned_at():
    assert promotion.judge(Stage.CHILD, useful=27, not_urgent=3).demote_advised is False
    assert promotion.judge(Stage.CHILD, useful=20, not_urgent=20).demote_advised is True
    assert (
        promotion.judge(Stage.CHILD, useful=2, not_urgent=8).demote_advised is False
    )  # too little to say (hysteresis)
    assert promotion.judge(Stage.INFANT, useful=0, not_urgent=40).demote_advised is False  # nothing below Infant
    assert promotion.judge(Stage.ADULT, useful=90, not_urgent=10).next_stage is None


def test_reliability_and_calibration_error():
    rows = [(0.9, True, "request")] * 9 + [(0.9, False, "request")] + [(0.1, False, "fyi")] * 10
    table = promotion.reliability(rows)
    cell = next(c for c in table if c["category"] == "request")
    assert (cell["n"], cell["predicted"], cell["observed"]) == (10, 0.9, 0.9)
    assert promotion.ece(rows) == pytest.approx(0.05, abs=1e-3)
    assert promotion.ece([]) is None


# --- the kNN head ---------------------------------------------------------------------------------


def test_it_says_nothing_with_too_few_labels_or_no_embedder(me, labelled):
    with session_scope() as s:
        assert knn.knn_urgency(s, NoEmbedder(), "the server is down") is None
        assert knn.knn_urgency(s, None, "the server is down") is None
        assert knn.knn_urgency(s, FakeEmbedder(), "   ") is None
    few = head("the server is down", exclude=None)
    assert few is not None  # eight labels is enough
    with session_scope() as s:
        for d in s.scalars(select(TriageDecision).limit(4)):
            d.feedback, d.feedback_via = None, None
    assert head("the server is down") is None  # four labels is not


def test_nearest_decisions_set_the_score(me, labelled):
    hot, cold = head("the server is down"), head("lunch on friday")
    assert hot is not None and cold is not None
    assert hot["score"] > 0.8 and cold["score"] < 0.2
    assert hot["n_labels"] == 8 and len(hot["neighbours"]) <= knn.K
    assert {n["label"] for n in hot["neighbours"]} == {"useful"}


def test_only_the_owners_own_button_presses_are_labels(me, labelled):
    # An inferred "useful" (the owner replied quickly) and a handled-answer judgement are not urgency labels.
    label(me, "lunch is urgent!", "useful", via="inferred")
    label(me, "lunch is urgent now", "good_handle")
    with session_scope() as s:
        assert len(knn._labelled(s)) == 8


def test_taking_a_label_back_removes_its_influence_and_its_vector(me, labelled):
    before = head("the server is down")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(TriageLabelVector)) == 8
        urgent_ids = [d.id for d in s.scalars(select(TriageDecision).where(TriageDecision.feedback == "useful"))]
        for did in urgent_ids:
            assert escalate.clear_feedback(s, s.get(TriageDecision, did)) is True
            assert escalate.clear_feedback(s, s.get(TriageDecision, did)) is False  # nothing left to clear
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(TriageLabelVector)) == 4  # the four calm ones remain
    after = head("the server is down")
    assert before is not None and before["score"] > 0.8
    assert after is None or all(n["label"] != "useful" for n in after["neighbours"])  # nothing urgent remains to find
    assert after is None or after["score"] < 0.2


def test_a_deleted_decision_takes_its_vector_with_it(me, labelled):
    head("the server is down")
    with session_scope() as s:
        d = s.scalar(select(TriageDecision).where(TriageDecision.feedback == "not_urgent"))
        s.delete(d)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(TriageLabelVector)) == 7


def test_leave_one_out_excludes_the_message_itself(me, labelled):
    with session_scope() as s:
        one = s.scalar(select(TriageDecision).where(TriageDecision.feedback == "useful"))
        assert one is not None
        text = s.scalar(select(Event.content).where(Event.id == one.inbound_event_id))
        out = knn.knn_urgency(s, FakeEmbedder(), str(text), exclude=one.id)
    assert out is not None and str(one.id) not in {n["decision_id"] for n in out["neighbours"]}


# --- wired into triage, as a shadow only ----------------------------------------------------------


def run_one(world, on, monkeypatch, *, knn_on: bool) -> TriageDecision:
    monkeypatch.setattr(get_settings(), "triage_knn_enabled", knn_on)
    monkeypatch.setattr("superaide.brain.embed.get_embedder", lambda settings: FakeEmbedder())
    with session_scope() as s:
        owner = s.get(Person, world["owner_id"])
        for t in URGENT:
            label(owner, t, "useful")
        for t in CALM:
            label(owner, t, "not_urgent")
    before = {d.id for d in decisions()}
    on.handle(sl(COLLEAGUE_SLACK, "the server is down"))
    drain()
    [new] = [d for d in decisions() if d.id not in before]
    return new


def test_off_nothing_is_recorded(world, on, monkeypatch):
    d = run_one(world, on, monkeypatch, knn_on=False)
    assert "knn" not in d.features


def test_on_the_score_is_recorded_and_the_decision_is_the_same(world, on, monkeypatch):
    # Decide one message with the head on, erase that decision and decide the same message again with it
    # off: identical decisions. The score is a note beside the decision, not an input to it.
    from superaide.triage import service
    from superaide.triage.models import Escalation

    got = run_one(world, on, monkeypatch, knn_on=True)
    assert got.features["knn"]["score"] > 0.8
    with session_scope() as s:
        s.query(Escalation).filter(Escalation.decision_id == got.id).delete()
        s.query(TriageDecision).filter(TriageDecision.id == got.id).delete()
    monkeypatch.setattr(get_settings(), "triage_knn_enabled", False)
    with session_scope() as s:
        again = service.triage_event(s, got.inbound_event_id, llm=None, model="m", ready=False)
        assert again is not None and "knn" not in again.features
        assert (again.decision, again.rule_hits, again.reason, again.features.get("score")) == (
            got.decision, got.rule_hits, got.reason, got.features.get("score"),
        )  # fmt: skip


def test_a_broken_head_never_touches_the_decision(world, on, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("embedder exploded")

    monkeypatch.setattr(knn, "knn_urgency", boom)
    d = run_one(world, on, monkeypatch, knn_on=True)
    assert "knn" not in d.features and d.decision in {"hold", "interrupt", "defer"}


def test_auc_and_the_leave_one_out_comparison(me, labelled):
    assert knn.auc([(0.9, True), (0.8, True), (0.2, False), (0.1, False)]) == 1.0
    assert knn.auc([(0.5, True), (0.5, False)]) == 0.5
    assert knn.auc([(0.5, True)]) is None
    with session_scope() as s:
        for d in s.scalars(select(TriageDecision)):
            d.features = {"score": 0.5}  # a model that can't tell them apart
        report = knn.compare(s, FakeEmbedder())
        assert knn.compare(s, NoEmbedder()) is None
    assert report is not None and report["labels"] == 8
    assert report["knn"]["auc"] == 1.0 and report["model"]["auc"] == 0.5


# --- the replay command (#117): `superaide triage-knn-compare` ----------------------------------------------


def _run_compare(monkeypatch, capsys, *argv, embedder=None):
    import sys

    from superaide import cli

    monkeypatch.setattr("superaide.brain.embed.get_embedder", lambda settings: embedder or FakeEmbedder())
    monkeypatch.setattr(sys, "argv", ["superaide", "triage-knn-compare", *argv])
    cli.main()
    return capsys.readouterr().out


def test_describe_says_too_few_with_a_handful_of_labels(me, labelled):
    with session_scope() as s:
        text = knn.describe(knn.compare(s, FakeEmbedder()))
    assert "Labelled decisions (your own button presses): 8" in text
    assert "kNN head: AUC 1.000 over 8 decisions" in text and "Too few to say anything" in text
    assert "changes no decision" in text


def test_describe_compares_once_there_are_enough_labels_and_says_nothing_without_an_embedder():
    enough = {"labels": 40, "knn": {"n": 40, "auc": 0.9}, "model": {"n": 40, "auc": 0.7}}
    assert "better than the model" in knn.describe(enough) and "+0.200" in knn.describe(enough)
    assert "worse than" in knn.describe({**enough, "model": {"n": 40, "auc": 0.95}})
    assert "about the same as" in knn.describe({**enough, "model": {"n": 40, "auc": 0.89}})
    assert "nothing to compare" in knn.describe({**enough, "model": {"n": 0, "auc": None}})
    assert "No embedder is ready" in knn.describe(None)


def test_the_command_prints_the_replay_and_changes_no_decision(me, labelled, monkeypatch, capsys):
    with session_scope() as s:
        before = [(d.id, d.decision, d.feedback) for d in s.scalars(select(TriageDecision).order_by(TriageDecision.id))]
    out = _run_compare(monkeypatch, capsys)
    assert "Labelled decisions (your own button presses): 8" in out and "kNN head: AUC" in out
    with session_scope() as s:
        after = [(d.id, d.decision, d.feedback) for d in s.scalars(select(TriageDecision).order_by(TriageDecision.id))]
    assert before == after


def test_the_command_counts_only_the_owners_own_labels(me, labelled, monkeypatch, capsys):
    label(me, "lunch is urgent!", "useful", via="inferred")  # not a button press
    assert "(your own button presses): 8" in _run_compare(monkeypatch, capsys)


def test_the_command_with_no_embedder_says_so(me, labelled, monkeypatch, capsys):
    assert "No embedder is ready" in _run_compare(monkeypatch, capsys, embedder=NoEmbedder())


def test_the_command_will_not_send_labelled_messages_to_a_remote_embedder_unless_told(
    me, labelled, monkeypatch, capsys
):
    class VoyageEmbedder(FakeEmbedder):  # the same class name the real remote embedder has
        pass

    with pytest.raises(SystemExit) as exc:
        _run_compare(monkeypatch, capsys, embedder=VoyageEmbedder())
    assert "Voyage" in str(exc.value) and "--allow-remote" in str(exc.value)
    assert "kNN head: AUC" in _run_compare(monkeypatch, capsys, "--allow-remote", embedder=VoyageEmbedder())
