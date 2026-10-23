"""Creator payout ledger (#132): rules, clawback, bond, null settlement. Integer minor units; no money moves."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.skills import payouts, receipts
from tests.api.conftest import signup

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
R = payouts.PayoutRules()


@pytest.fixture(autouse=True)
def flags_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "skill_receipts_enabled", True)
    monkeypatch.setattr(get_settings(), "payouts_enabled", True)


def row(seq, *, owner="o1", outcome="approved_unedited", latency=30, batch=False, day=0, skill="acme/notes"):
    return {"seq": seq, "skill_id": skill, "version": "1.0.0", "effect_class": "write_own", "outcome": outcome,
            "owner_ref": owner, "ref_seq": None, "latency_s": latency, "batch": batch,
            "at": (T0 + timedelta(days=day, minutes=seq)).isoformat()}  # fmt: skip


def test_money_is_whole_minor_units():
    assert payouts.split(50, 2000) == (40, 10)
    assert payouts.split(33, 2000) == (27, 6)  # the fee rounds down, the creator keeps the remainder
    assert sum(payouts.split(33, 2000)) == 33
    sig = inspect.signature(payouts.split)
    assert all(p.annotation in ("int", int) for p in sig.parameters.values())


def test_only_real_approvals_earn():
    rows = [row(1), row(2, latency=2), row(3, batch=True), row(4, outcome="edited"), row(5, outcome="rejected")]
    earned, refused = payouts.qualify(rows, R)
    assert [x["seq"] for x in earned] == [1]
    assert refused == {"under_floor": 1, "batch_approved": 1}


def test_a_nagging_skill_earns_up_to_the_daily_cap_only():
    rows = [row(i + 1) for i in range(25)]  # 25 slow, single approvals from one owner on one day
    earned, refused = payouts.qualify(rows, R)
    assert len(earned) == R.daily_owner_cap and refused == {"over_daily_cap": 15}
    assert {"flag": "nagging", "busiest_day": 25}.items() <= payouts.detect_abuse(rows, R)[0].items()


def test_an_auto_approve_loop_is_detected_and_earns_nothing():
    rows = [row(i + 1, latency=1, owner="bot") for i in range(12)] + [row(20, owner="human")]
    sim = payouts.simulate(rows, R)
    assert sim["outcomes_paid"] == 1 and sim["refused"] == {"under_floor": 12}
    flags = {(a["owner_ref"], a["flag"]) for a in sim["abuse"]}
    assert ("bot", "auto_approve_loop") in flags and ("human", "auto_approve_loop") not in flags
    batch = [row(i + 1, batch=True, owner="b") for i in range(6)]
    assert payouts.detect_abuse(batch, R)[0]["flag"] == "batch_approving"


def test_simulation_reports_what_would_be_paid_without_writing():
    sim = payouts.simulate([row(i + 1, day=i) for i in range(4)], R)
    assert (sim["outcomes_paid"], sim["creator_minor"], sim["platform_fee_minor"]) == (4, 160, 40)


def add(s, n=1, **kw):
    for i in range(n):
        receipts.append(
            s, skill_id=kw.get("skill_id", "acme/notes"), version="1.0.0", effect_class=kw.get("effect", "write_own"),
            outcome="approved_unedited", latency_s=30, at=kw.get("at", T0) + timedelta(minutes=i),
        )  # fmt: skip


def test_accrual_clawback_and_the_payable_window():
    with session_scope() as s:
        add(s, 3)
        assert payouts.accrue(s) == {"accruals": 3, "clawbacks": 0, "skipped_no_bond": 0}
        assert payouts.accrue(s)["accruals"] == 0  # once each
        assert payouts.payable(s, now=T0 + timedelta(days=1)) == {}  # still inside the clawback window
        assert payouts.payable(s, now=T0 + timedelta(days=8)) == {"acme": 120}
        receipts.append(s, skill_id="acme/notes", version="1.0.0", effect_class="write_own", outcome="reverted",
                        ref_seq=1, at=T0 + timedelta(days=2))  # fmt: skip
        assert payouts.accrue(s)["clawbacks"] == 1
        assert payouts.payable(s, now=T0 + timedelta(days=8)) == {"acme": 80}
        # a revert after the window is not clawed back
        receipts.append(s, skill_id="acme/notes", version="1.0.0", effect_class="write_own", outcome="reverted",
                        ref_seq=2, at=T0 + timedelta(days=30))  # fmt: skip
        assert payouts.accrue(s)["clawbacks"] == 0


def test_official_skills_earn_nothing():
    with session_scope() as s:
        add(s, 2, skill_id="superaide/reminders")
        assert payouts.accrue(s)["accruals"] == 0


def test_send_and_spend_skills_need_a_bond_and_refunds_come_out_of_it():
    with session_scope() as s:
        add(s, 2, skill_id="acme/mailer", effect="send")
        # outside the catalog a skill counts as read-only (the bond gate is tested with a patched creator below)
        assert payouts.creator_of("acme/mailer") == ("acme", "read_public")
        assert payouts.bond_balance(s, "acme") == 0
        payouts.post_bond(s, "acme", 5000)
        assert payouts.bond_balance(s, "acme") == 5000
        with pytest.raises(payouts.PayoutError) as exc:
            payouts.refund_from_bond(s, "acme", "acme/mailer", 6000, "too much")
        assert exc.value.status == 409
        payouts.refund_from_bond(s, "acme", "acme/mailer", 1200, "wrong recipient")
        assert payouts.bond_balance(s, "acme") == 3800


def test_bond_gates_accrual_for_send_skills(monkeypatch):
    monkeypatch.setattr(payouts, "creator_of", lambda skill_id: ("acme", "send"))
    with session_scope() as s:
        add(s, 2, skill_id="acme/mailer", effect="send")
        assert payouts.accrue(s) == {"accruals": 0, "clawbacks": 0, "skipped_no_bond": 2}
        payouts.post_bond(s, "acme", 5000)
        assert payouts.accrue(s)["accruals"] == 2


def test_the_null_provider_moves_nothing():
    with session_scope() as s:
        add(s, 3)
        payouts.accrue(s)
        report = payouts.settle_due(s, now=T0 + timedelta(days=9))
        assert report.provider == "null" and report.due == {"acme": 120} and report.settled_minor == 0
        assert payouts.payable(s, now=T0 + timedelta(days=9)) == {"acme": 120}  # unchanged: nothing was paid
        assert set(payouts.PROVIDERS) == {"null"}


def test_a_provider_that_reports_money_moved_writes_a_settlement_line():
    class Fake:
        name = "fake"

        def settle(self, creator, amount_minor, currency, key):
            return payouts.SettlementResult("settled", amount_minor, "test")

    with session_scope() as s:
        add(s, 3)
        payouts.accrue(s)
        now = T0 + timedelta(days=9)
        assert payouts.settle_due(s, now=now, with_provider=Fake()).settled_minor == 120
        assert payouts.payable(s, now=now) == {"acme": 0}


def test_an_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "payout_provider", "stripe")
    with session_scope() as s, pytest.raises(payouts.PayoutError):
        payouts.settle_due(s)


def test_flag_off_does_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "payouts_enabled", False)
    with session_scope() as s:
        add(s, 2)
        assert payouts.accrue(s)["accruals"] == 0
        with pytest.raises(payouts.PayoutError):
            payouts.post_bond(s, "acme", 100)


def test_the_owner_routes(owner, browser, monkeypatch):
    with session_scope() as s:
        add(s, 2)
    assert owner.post("/app/skill-payouts/accrue").json()["made"]["accruals"] == 2
    shown = owner.get("/app/skill-payouts").json()
    assert shown["moves_money"] is False and shown["provider"] == "null" and shown["simulation"]["outcomes_paid"] == 2
    assert owner.post("/app/skill-payouts/bond", json={"creator": "acme", "amount_minor": 5000}).json()["bonds"] == {
        "acme": 5000
    }
    assert owner.post("/app/skill-payouts/bond", json={"creator": "acme", "amount_minor": 0}).status_code == 422
    big = {"creator": "acme", "skill_id": "acme/notes", "amount_minor": 9000}
    assert owner.post("/app/skill-payouts/refund", json=big).status_code == 409
    assert owner.post("/app/skill-payouts/settle").json()["settled_minor"] == 0
    monkeypatch.setattr(get_settings(), "payouts_enabled", False)
    assert owner.get("/app/skill-payouts").status_code == 404
    assert browser().get("/app/skill-payouts").status_code == 401


def test_a_stranger_sees_nothing(browser):
    stranger = browser()
    signup(stranger, name="Mallory Tan", email="mallory@example.com")
    assert stranger.get("/app/skill-payouts").status_code == 403
