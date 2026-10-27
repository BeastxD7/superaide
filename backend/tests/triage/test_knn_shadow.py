"""The kNN head is shadow-only and says so (#117): agreement with corrections, never eligible below the sample."""

from __future__ import annotations

from sqlalchemy import select

from superaide.memory.models import Event
from superaide.platform.db.session import session_scope
from superaide.triage import knn
from superaide.triage.models import TriageDecision
from superaide.triage.types import Feedback


def add(s, ctx, i, score, feedback):
    e = Event(context_id=ctx.id, direction="in", content=f"msg {i}", webhook_verified=True)
    s.add(e)
    s.flush()
    feats = {"knn": {"score": score}} if score is not None else {}
    s.add(
        TriageDecision(
            inbound_event_id=e.id, context_id=ctx.id, features=feats, feedback=feedback, feedback_via="app",
            decision="handle", reason="x", stage_at_time="infant", policy_version="v1",
        )
    )  # fmt: skip
    s.flush()


def world(s):
    from superaide.memory.models import Context

    c = Context(provider="telegram", tenant_scope="", external_id="c", kind="dm", audience_class="private_1to1")
    s.add(c)
    s.flush()
    return c


def test_off_says_nothing():
    with session_scope() as s:
        st = knn.shadow_status(s, enabled=False)
        assert st["text"] == "" and not st["eligible"] and st["mode"] == "shadow"


def test_the_status_line_reports_agreement_over_the_corrections_with_a_recorded_score():
    with session_scope() as s:
        c = world(s)
        for i in range(10):  # urgent when the score is high; agrees on 7 of 10
            add(s, c, i, 0.9 if i < 7 else 0.1, Feedback.USEFUL)
        add(s, c, 99, None, Feedback.NOT_URGENT)  # no score recorded: not compared
        st = knn.shadow_status(s, enabled=True)
        assert (st["n"], st["agree"], st["agreement"]) == (10, 7, 0.7)
        assert st["text"] == "Shadow: agrees with you 70% over 10 corrections, needs 200."
        assert not st["eligible"]


def test_never_eligible_below_the_minimum_sample_however_good_the_agreement():
    with session_scope() as s:
        c = world(s)
        for i in range(60):
            add(s, c, i, 0.95, Feedback.USEFUL)
        st = knn.shadow_status(s, enabled=True)
        assert st["agreement"] == 1.0 and st["lower_bound"] > 0.9 and not st["eligible"]


def test_eligible_only_with_enough_corrections_and_a_high_lower_bound():
    with session_scope() as s:
        c = world(s)
        for i in range(200):
            add(s, c, i, 0.95, Feedback.USEFUL)
        assert knn.shadow_status(s, enabled=True)["eligible"]
    with session_scope() as s:
        for d in s.scalars(select(TriageDecision).limit(60)):
            d.features = {"knn": {"score": 0.05}}  # now it disagrees on 60 of 200
        s.flush()
        st = knn.shadow_status(s, enabled=True)
        assert st["agreement"] == 0.7 and not st["eligible"]


def test_nothing_in_the_decision_path_reads_the_knn_score():
    import pathlib

    import superaide.triage as pkg

    root = pathlib.Path(pkg.__file__).parent
    for name in ("policy.py", "rules.py", "escalate.py", "classify.py", "buttons.py"):
        # policy.py only receives the promoted head's yes/no as `raise_urgent` and labels the hit "knn_raise";
        # it never imports or reads the score (#117 raise-only head, tests/test_knn_raise.py).
        text = (root / name).read_text().replace("knn.forget", "").replace("import knn", "").replace("knn_raise", "")
        assert "knn" not in text, name
