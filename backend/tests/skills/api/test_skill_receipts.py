"""Receipt log and computed tiers (#131): hash chain, tamper detection, an outside script that recomputes tiers."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.skills import claim, receipts, tier_rules
from tests.api_support import signup

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "skill_tier.py"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def flag_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "skill_receipts_enabled", True)


def chain(rows_spec: list[tuple[str, str, int, str]]) -> list[dict]:
    """Build a valid chain from (owner_ref, outcome, day offset, skill version)."""
    out, prev = [], tier_rules.GENESIS
    for i, (owner, outcome, day, version) in enumerate(rows_spec, start=1):
        row = {"seq": i, "skill_id": "acme/mail", "version": version, "effect_class": "send", "outcome": outcome,
               "owner_ref": owner, "ref_seq": None, "latency_s": 30, "batch": False,
               "at": (T0 + timedelta(days=day)).isoformat()}  # fmt: skip
        row["prev_hash"] = prev
        row["entry_hash"] = tier_rules.entry_digest(prev, tier_rules.fields_of(row))
        prev = row["entry_hash"]
        out.append(row)
    return out


def population(owners: int, per_owner: int, outcome: str = "approved_unedited", extra=()):
    spec = [(f"o{o}", outcome, d, "1.0.0") for o in range(owners) for d in range(per_owner)]
    return chain([*spec, *extra])


def test_the_chain_verifies_and_any_change_breaks_it():
    rows = population(2, 4)
    assert tier_rules.verify_chain(rows) == (True, None, "")
    bad = copy.deepcopy(rows)
    bad[3]["outcome"] = "rejected"
    assert tier_rules.verify_chain(bad)[:2] == (False, 4)
    gone = rows[:2] + rows[3:]  # a deleted receipt
    assert tier_rules.verify_chain(gone)[0] is False
    swapped = copy.deepcopy(rows)
    swapped[1]["prev_hash"] = "f" * 64
    assert tier_rules.verify_chain(swapped)[:2] == (False, 2)


def test_tiers_are_computed_from_the_receipts():
    assert tier_rules.compute_tier(population(1, 2))["tier"] == "new"
    # five owners over many days, all approved unedited: trusted
    assert tier_rules.compute_tier(population(5, 20))["tier"] == "trusted"
    # enough receipts but only two owners: observed at most
    assert tier_rules.compute_tier(population(2, 20))["tier"] == "new"
    assert tier_rules.compute_tier(population(4, 10))["tier"] == "observed"
    # reverts drive it down
    rows = population(5, 20, extra=[(f"o{i % 5}", "reverted", 5 + i, "1.0.0") for i in range(15)])
    assert tier_rules.compute_tier(rows)["tier"] == "demoted"


def test_one_owner_cannot_move_a_tier_by_nagging():
    rows = chain([("spam", "approved_unedited", 0, "1.0.0")] * 500)  # all on the same day
    assert tier_rules.compute_tier(rows)["counted"] == 0
    capped = chain([("big", "approved_unedited", d % 30, "1.0.0") for d in range(500)])
    assert tier_rules.compute_tier(capped)["counted"] == tier_rules.Rules().owner_cap


def test_the_outside_script_recomputes_the_tiers_and_catches_tampering(tmp_path):
    rows = population(5, 20)
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"rules": tier_rules.Rules().__dict__, "receipts": rows}))
    ran = subprocess.run([sys.executable, str(SCRIPT), str(good)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    [tier] = json.loads(ran.stdout)
    assert tier["tier"] == "trusted" and tier == {**tier_rules.compute_all(rows)[0]}
    bad_rows = copy.deepcopy(rows)
    bad_rows[40]["outcome"] = "edited"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"receipts": bad_rows}))
    ran = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert ran.returncode == 1 and "CHAIN BROKEN" in ran.stderr and "receipt 41" in ran.stderr


def test_receipts_are_written_chained_and_hold_no_content(owner):
    with session_scope() as s:
        a = receipts.append(s, skill_id="superaide/gmail", version="1.0.0", effect_class="send",
                            outcome="approved_unedited", latency_s=40)  # fmt: skip
        b = receipts.append(s, skill_id="superaide/gmail", version="1.0.0", effect_class="send", outcome="rejected")
        assert a and b and b.prev_hash == a.entry_hash and (a.seq, b.seq) == (1, 2)
        assert receipts.verify(s)["ok"]
        data = receipts.export(s)
        assert set(data["receipts"][0]) == {*tier_rules.HASHED_FIELDS, "prev_hash", "entry_hash"}
        assert data["daily_roots"] and receipts.owner_ref("superaide/gmail") != receipts.owner_ref("superaide/memory")
        # tamper with the stored row: verification names it
        a.outcome = "edited"
        s.flush()
        assert receipts.verify(s) == {"ok": False, "broken_at": 1, "reason": "a receipt was changed"}


def test_flag_off_writes_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "skill_receipts_enabled", False)
    with session_scope() as s:
        assert receipts.append(s, skill_id="a/b", version="1", effect_class="send", outcome="rejected") is None


def test_an_approval_decision_writes_a_receipt(owner):
    from superaide.platform.db.models import Approval
    from superaide.policy.actions import resolve

    with session_scope() as s:
        ap = Approval(kind="gmail_send", payload={}, status="pending")
        s.add(ap)
        s.flush()
        resolve(s, ap, approve=False, decided_by="owner")
        [row] = receipts.rows(s)
        assert (row["skill_id"], row["effect_class"], row["outcome"]) == ("superaide/gmail", "send", "rejected")
        other = Approval(kind="not_a_skill_tool", payload={}, status="pending")
        s.add(other)
        s.flush()
        resolve(s, other, approve=False, decided_by="owner")
        assert len(receipts.rows(s)) == 1  # not a skill's action: no receipt


def test_a_revert_receipt_points_at_the_original():
    with session_scope() as s:
        first = receipts.append(s, skill_id="superaide/gmail", version="1.0.0", effect_class="send",
                                outcome="approved_unedited")  # fmt: skip
        assert first
        r = receipts.record_revert(s, first.seq)
        assert (r.outcome, r.ref_seq) == ("reverted", 1)
        with pytest.raises(LookupError):
            receipts.record_revert(s, r.seq)  # you can't revert a revert


def test_the_claim_is_bucketed_signed_expiring_and_checked_against_the_log():
    with session_scope() as s:
        assert claim.issue(s, "agent-b") is None  # too little in the log
        for i in range(12):
            receipts.append(s, skill_id="superaide/gmail", version="1.0.0", effect_class="send",
                            outcome="approved_unedited" if i < 11 else "edited")  # fmt: skip
        now = time.time()
        t1, t2 = claim.issue(s, "agent-b", now=now), claim.issue(s, "agent-c", now=now)
        assert t1 and t2
        p1, p2 = claim.verify(t1, now=now), claim.verify(t2, now=now)
        assert p1 and p2 and p1["approved_unedited_pct"] == 90 and p1["sample"] == "10+"
        assert p1["ref"] != p2["ref"]  # a different reference for every counterparty
        assert set(p1) == {"claim", "effect_class", "approved_unedited_pct", "sample", "ref", "iat", "exp"}
        assert claim.check_against_log(s, t1, now=now)
        assert claim.verify(t1, now=now + 25 * 3600) is None  # expired
        assert claim.verify(t1[:-2] + "xx", now=now) is None  # signature
        for _ in range(12):  # the log now says something different
            receipts.append(s, skill_id="superaide/gmail", version="1.0.0", effect_class="send", outcome="rejected")
        assert not claim.check_against_log(s, t1, now=now)


def test_the_owner_routes(owner, browser, monkeypatch):
    with session_scope() as s:
        receipts.append(
            s, skill_id="superaide/gmail", version="1.0.0", effect_class="send", outcome="approved_unedited"
        )
    shown = owner.get("/app/skill-receipts").json()
    assert shown["count"] == 1 and shown["chain"]["ok"] and shown["tiers"][0]["tier"] == "new"
    exported = owner.get("/app/skill-receipts/export").json()
    assert exported["format"] == "superaide-skill-receipts/1" and len(exported["receipts"]) == 1
    assert owner.post("/app/skill-receipts/revert", json={"seq": 1}).status_code == 200
    assert owner.post("/app/skill-receipts/revert", json={"seq": 99}).status_code == 404
    assert owner.post("/app/skill-receipts/claim", json={"counterparty": "b"}).status_code == 409
    monkeypatch.setattr(get_settings(), "skill_receipts_enabled", False)
    assert owner.get("/app/skill-receipts").status_code == 404
    assert browser().get("/app/skill-receipts").status_code == 401


def test_a_stranger_sees_nothing(browser):
    stranger = browser()
    signup(stranger, name="Mallory Tan", email="mallory@example.com")
    assert stranger.get("/app/skill-receipts/export").status_code == 403
