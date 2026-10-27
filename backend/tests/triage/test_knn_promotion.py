# ruff: noqa: F811  (the triage tests' fixtures are imported, then used by name)
"""#117: promotion of the kNN head is the owner's decision. Manual by default; eligible only on z 1.96, at least 200
corrections in 30 days and a 0.8 lower bound; demoted after 14 days under the gate; and a promoted head, behind
TRIAGE_KNN_RAISE_ENABLED, may only RAISE a calm message, never silence or lower one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.brain.embed import FakeEmbedder
from superaide.identity.models import Person
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_state
from superaide.triage import knn, knn_promotion
from superaide.triage.models import TriageDecision
from superaide.triage.types import Feedback
from tests.conftest import COLLEAGUE_SLACK, sl
from tests.triage.test_knn_shadow import add
from tests.triage.test_knn_shadow import world as ctx_world
from tests.triage.test_triage_escalation import decisions, drain, on, pushes, world  # noqa: F401  (fixtures)
from tests.triage.test_triage_knn import CALM, URGENT, label

NOW = datetime.now(UTC)


def fill(s, agree: int, total: int, *, age_days: int = 0):
    c = ctx_world(s)
    for i in range(total):
        add(s, c, i, 0.95 if i < agree else 0.05, Feedback.USEFUL)  # the first `agree` agree with the owner
    for d in s.scalars(select(TriageDecision)):
        d.created_at = NOW - timedelta(days=age_days)
    s.flush()


# --- the numbers: z 1.96, 200 corrections, 30-day window -----------------------------------------------------


def test_the_gate_numbers_are_the_owners_choice():
    with session_scope() as s:
        st = knn.shadow_status(s, enabled=True)
    assert (st["z"], st["needs"], st["gate"], st["window_days"]) == (1.96, 200, 0.8, 30)


def test_two_hundred_good_corrections_in_the_window_are_eligible():
    with session_scope() as s:
        fill(s, 190, 200)
        assert knn.shadow_status(s, enabled=True)["eligible"]


def test_one_fewer_than_two_hundred_is_not_eligible():
    with session_scope() as s:
        fill(s, 199, 199)
        assert not knn.shadow_status(s, enabled=True)["eligible"]


def test_corrections_older_than_thirty_days_do_not_count():
    with session_scope() as s:
        fill(s, 200, 200, age_days=31)
        st = knn.shadow_status(s, enabled=True)
        assert st["n"] == 0 and not st["eligible"]


def test_the_95_percent_bound_is_stricter_than_the_old_one():
    with session_scope() as s:
        fill(s, 166, 200)  # 83% agreement: lower bound ~0.77 at z 1.96 (it was ~0.78 at 1.645), under 0.8
        st = knn.shadow_status(s, enabled=True)
        assert st["agreement"] == 0.83 and st["lower_bound"] < 0.8 and not st["eligible"]


# --- Promote is the owner's press, and only when eligible --------------------------------------------------------


def test_promotion_is_refused_until_eligible():
    with session_scope() as s:
        fill(s, 20, 20)
        with pytest.raises(knn_promotion.PromotionError) as e:
            knn_promotion.promote(s, by="owner:x")
        assert e.value.status == 409 and "20 of the 200" in str(e.value)
        assert not knn_promotion.is_promoted(s)


def test_promotion_is_recorded_with_the_evidence_and_audited():
    with session_scope() as s:
        fill(s, 190, 200)
        rec = knn_promotion.promote(s, by="owner:jordan")
        assert rec["promoted"] and rec["promoted_by"] == "owner:jordan"
        assert rec["evidence"]["n"] == 200 and rec["evidence"]["z"] == 1.96
        assert knn_promotion.is_promoted(s)
        assert knn_promotion.promote(s, by="owner:again")["promoted_by"] == "owner:jordan"  # idempotent
    with session_scope() as s:
        assert len(list(s.scalars(select(AuditLog).where(AuditLog.action == "triage_knn_promoted")))) == 1


def test_the_owner_can_take_it_back_at_any_time():
    with session_scope() as s:
        fill(s, 190, 200)
        knn_promotion.promote(s, by="owner:x")
        out = knn_promotion.demote(s, reason="taken back by you", by="owner:x")
        assert out["promoted"] is False and not knn_promotion.is_promoted(s)
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "triage_knn_demoted")) is not None


# --- demotion: 14 days under the gate ----------------------------------------------------------------------------


def _promoted_then_decayed(s):
    fill(s, 190, 200)
    knn_promotion.promote(s, by="owner:x", now=NOW)
    for d in s.scalars(select(TriageDecision)):
        d.created_at = NOW - timedelta(days=40)  # the evidence ages out of the window: under the gate


def test_under_the_gate_starts_a_clock_and_demotes_only_after_14_days():
    with session_scope() as s:
        _promoted_then_decayed(s)
        t0 = NOW + timedelta(days=1)
        cur = knn_promotion.review(s, now=t0, force=True)
        assert cur["promoted"] and cur["below_since"] == t0.isoformat()
        assert knn_promotion.review(s, now=t0 + timedelta(days=13), force=True)["promoted"]
        done = knn_promotion.review(s, now=t0 + timedelta(days=14), force=True)
        assert done["promoted"] is False and "14 days" in done["demoted_reason"]
        assert not knn_promotion.is_promoted(s)


def test_recovering_above_the_gate_resets_the_clock():
    with session_scope() as s:
        _promoted_then_decayed(s)
        t0 = NOW + timedelta(days=1)
        knn_promotion.review(s, now=t0, force=True)
        for d in s.scalars(select(TriageDecision)):
            d.created_at = t0 + timedelta(days=5)  # fresh again
        s.flush()
        cur = knn_promotion.review(s, now=t0 + timedelta(days=6), force=True)
        assert cur["promoted"] and cur["below_since"] is None


def test_review_is_throttled_to_once_an_hour():
    with session_scope() as s:
        _promoted_then_decayed(s)
        knn_promotion.review(s, now=NOW + timedelta(minutes=5))
        assert knn_promotion.state(s)["below_since"] is None  # too soon after the promotion: not looked at yet


# --- the raise-only head --------------------------------------------------------------------------------------------


@pytest.fixture
def armed(world, on, monkeypatch):
    """Head on and labelled, owner at the Adult stage with quiet hours off, the message not matched by any rule."""
    s = get_settings()
    monkeypatch.setattr(s, "triage_knn_enabled", True)
    monkeypatch.setattr(s, "triage_knn_raise_enabled", True)
    monkeypatch.setattr(s, "triage_enabled", True)
    monkeypatch.setattr("superaide.brain.embed.get_embedder", lambda settings: FakeEmbedder())
    with session_scope() as db:
        owner = db.get(Person, world["owner_id"])
        for t in URGENT:
            label(owner, t, "useful")
        for t in CALM:
            label(owner, t, "not_urgent")
        set_state(db, "stage", {"stage": "adult"})
    return world


def decide(on, text: str) -> TriageDecision:
    """The decision triage makes for one colleague message (the offline model never calls it urgent)."""
    before = {d.id for d in decisions()}
    on.handle(sl(COLLEAGUE_SLACK, text))
    drain()
    [new] = [d for d in decisions() if d.id not in before]
    return new


def promote_now():
    with session_scope() as db:
        set_state(db, knn_promotion.STATE_KEY, {"promoted": True, "promoted_by": "owner:test",
                                               "promoted_at": NOW.isoformat(), "below_since": None,
                                               "reviewed_at": NOW.isoformat()})  # fmt: skip


def test_promoted_and_flagged_a_calm_message_the_head_calls_urgent_is_raised(armed, on):
    promote_now()
    d = decide(on, "database down right now")
    assert d.features["knn"]["score"] >= 0.8 and d.features.get("knn_raised") is True
    assert d.decision == "interrupt" and "knn_raise" in d.rule_hits


def test_not_promoted_the_same_message_stays_held(armed, on):
    d = decide(on, "database down right now")
    assert d.features["knn"]["score"] >= 0.8 and not d.features.get("knn_raised") and d.decision == "hold"


def test_raise_flag_off_the_promotion_changes_nothing(armed, on, monkeypatch):
    promote_now()
    monkeypatch.setattr(get_settings(), "triage_knn_raise_enabled", False)
    d = decide(on, "database down right now")
    assert not d.features.get("knn_raised") and d.decision == "hold"


def test_a_low_head_score_never_lowers_or_silences_anything(armed, on):
    promote_now()
    calm = decide(on, "lunch on friday")
    assert calm.features["knn"]["score"] < 0.2 and calm.decision == "hold" and not calm.features.get("knn_raised")


def test_a_topic_the_owner_silenced_is_not_raised(armed, on):
    """Silencing is the owner's own instruction (rule R2): the head cannot undo it."""
    from superaide.triage.store import settings_for

    promote_now()
    with session_scope() as db:
        settings_for(db, db.get(Person, armed["owner_id"])).silenced_topics = ["database"]
    d = decide(on, "database down right now")
    assert d.features["knn"]["score"] >= 0.8 and d.decision == "hold" and not d.features.get("knn_raised")


def test_at_the_infant_stage_a_raise_is_only_an_in_app_note(armed, on):
    """The stage cap still applies to a raised message: Infant never pushes on the model's (or the head's) say."""
    promote_now()
    with session_scope() as db:
        set_state(db, "stage", {"stage": "infant"})
    d = decide(on, "database down right now")
    assert d.decision == "hold" and d.features["maybe_urgent"] is True


def test_the_off_state_records_nothing_about_a_raise(world, on, monkeypatch):
    monkeypatch.setattr(get_settings(), "triage_knn_enabled", False)
    on.handle(sl(COLLEAGUE_SLACK, "database down right now"))
    assert all("knn_raised" not in d.features for d in decisions())
