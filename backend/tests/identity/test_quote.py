"""Quote and Odds (#139): odds from logged outcomes only, with n; low odds prepare instead of act. Off by default."""

from __future__ import annotations

import pytest

from superaide.approvals import quote
from superaide.approvals.binding import seal
from superaide.approvals.models import Approval
from superaide.identity.models import Person
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.policy import actions


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "quote_odds_enabled", True)


def history(kind: str, ok: int, failed: int) -> None:
    with session_scope() as s:
        for outcome, n in (("ok", ok), ("failed", failed)):
            for _ in range(n):
                s.add(Approval(kind=kind, status="approved", payload={"outcome": outcome}))


def ask(kind="gmail_send") -> Approval:
    with session_scope() as s:
        p = Person(display_name="Jordan", relation="manager")
        s.add(p)
        s.flush()
        sender = Sender(identity=None, person=p, trust=int(Trust.STEPPED_UP))
        rules = {**actions.RULES, kind: actions.Rule(int(Trust.LINKED), True, True)}
        a = actions.decide(s, kind=kind, summary="x", requester=sender, rules=rules).approval
        s.expunge(a)
        return a


def test_off_by_default_nothing_is_added():
    history("gmail_send", 1, 9)
    assert "quote" not in ask().payload and "notes" not in ask().payload


def test_without_enough_history_there_are_no_odds_only_n(on):
    history("gmail_send", 2, 1)
    a = ask()
    assert a.payload["quote"] == {"n": 3, "ok": 2, "prepare": False}
    assert "n=3" in a.payload["notes"][0] and "No odds" in a.payload["notes"][0]


def test_good_odds_are_shown_with_the_sample_size(on):
    history("gmail_send", 8, 2)
    a = ask()
    assert a.payload["quote"]["rate"] == 0.8 and not a.payload["quote"]["prepare"]
    assert "8 of 10" in a.payload["notes"][0] and "n=10" in a.payload["notes"][0]


def test_low_odds_prepare_instead_of_act(on):
    history("gmail_send", 2, 8)
    a = ask()
    assert a.payload["quote"]["prepare"] is True
    ran = []
    previous = actions.EXECUTORS.get("gmail_send")
    actions.EXECUTORS["gmail_send"] = lambda s, ap: ran.append(1) or "Sent."
    try:
        with session_scope() as s:
            ap = s.get(Approval, a.id)
            actions.resolve(s, ap, approve=True, decided_by="owner")
            assert ap.status == "approved" and ran == [] and ap.payload["result"].startswith("Prepared")
    finally:
        if previous is None:
            del actions.EXECUTORS["gmail_send"]
        else:
            actions.EXECUTORS["gmail_send"] = previous


def test_odds_are_per_kind(on):
    history("calendar_move_event", 0, 9)
    assert ask("gmail_send").payload["quote"]["n"] == 0


def test_resolve_records_the_outcome_for_next_time():
    ok = Approval(kind="x_ok", payload={})
    bad = Approval(kind="x_bad", payload={})
    seal(ok)
    seal(bad)
    with session_scope() as s:
        s.add_all([ok, bad])
        s.flush()
        actions.EXECUTORS["x_ok"] = lambda s, a: "Sent."
        actions.EXECUTORS["x_bad"] = lambda s, a: "Approved, but Google said no."
        try:
            actions.resolve(s, ok, approve=True, decided_by="o")
            actions.resolve(s, bad, approve=True, decided_by="o")
        finally:
            del actions.EXECUTORS["x_ok"], actions.EXECUTORS["x_bad"]
        assert (ok.payload["outcome"], bad.payload["outcome"]) == ("ok", "failed")
        assert quote.odds(s, "x_bad") == (1, 0)
