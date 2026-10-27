"""The Mirror (/app/mirror): patterns counted in code from the owner's own rows, each with receipts."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.brain.models import SpendEntry
from superaide.features.mirror import patterns, service
from superaide.features.mirror.models import MirrorNote
from superaide.intentions.models import Intention
from superaide.memory.models import Context, Event
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def flag_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(get_settings(), "mirror_enabled", True)


def person_id(email: str) -> uuid.UUID:
    return account(email).person_id


def reminder(owner: uuid.UUID, text: str, *, weeks_ago: int = 0, status="open", delivered=False, due_days_ago=0):
    created = NOW - timedelta(weeks=weeks_ago)
    return Intention(
        owner_person_id=owner,
        text=text,
        due_at=NOW - timedelta(days=due_days_ago),
        status=status,
        created_at=created,
        delivered_at=NOW - timedelta(days=due_days_ago) if delivered else None,
    )


def seed(owner: uuid.UUID) -> None:
    with session_scope() as s:
        for w in (0, 1, 2, 3):
            s.add(reminder(owner, "Send the investor update", weeks_ago=w, status="done"))
        s.add(reminder(owner, "Call the landlord", weeks_ago=1, delivered=True, due_days_ago=6))
        s.add(reminder(owner, "Renew the passport", weeks_ago=2, delivered=True, due_days_ago=10))
        s.add(reminder(owner, "Water the plants", weeks_ago=0))  # not delivered, not old: no finding
        ctx = s.scalar(select(Context).where(Context.external_id == f"web:{owner}"))
        if ctx is None:
            ctx = Context(
                provider="web", tenant_scope="", external_id=f"web:{owner}", kind="dm", audience_class="private_1to1"
            )
            s.add(ctx)
            s.flush()
        for days, n in ((3, 9), (10, 5)):  # nine messages this week, five last week
            for i in range(n):
                s.add(
                    Event(
                        context_id=ctx.id, direction="in", resolved_person_id=owner, content=f"message {i}",
                        trust_at_time=3, audience_person_ids=[owner], audience_has_unknown=False,
                        created_at=NOW - timedelta(days=days, minutes=i),
                    )
                )  # fmt: skip


def opted_in(owner_client) -> None:
    assert owner_client.post("/app/mirror/settings", json={"enabled": True}).json()["enabled"] is True


def test_it_is_404_while_the_flag_is_off_but_strangers_are_refused_first(browser, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "mirror_enabled", False)
    for method, path in (("GET", "/app/mirror"), ("POST", "/app/mirror/settings")):
        assert owner.request(method, path, json={"enabled": True} if method == "POST" else None).status_code == 404
        assert browser().request(method, path, json={}).status_code == 401
    unhired = browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    assert unhired.get("/app/mirror").status_code == 403


def test_an_owner_has_to_opt_in(owner):
    seed(person_id(OWNER["email"]))
    first = owner.get("/app/mirror").json()
    assert first == {"enabled": False, "note": None, "earlier": [], "muted": 0}
    opted_in(owner)
    assert owner.get("/app/mirror").json()["note"] is not None


def test_three_findings_each_with_receipts_the_owner_can_open(owner):
    me = person_id(OWNER["email"])
    seed(me)
    opted_in(owner)
    note = owner.get("/app/mirror").json()["note"]
    by_kind = {f["kind"]: f for f in note["findings"]}
    assert set(by_kind) == {"repeated_reminders", "dropped_commitments", "activity_shift"}
    rep = by_kind["repeated_reminders"]
    assert rep["text"] == "You set a reminder for “Send the investor update” 4 times across 4 weeks."
    assert len(rep["receipts"]) == 4 and rep["href"] == "/dashboard/reminders"
    assert {r["label"] for r in rep["receipts"]} == {"Send the investor update"}
    drop = by_kind["dropped_commitments"]
    assert drop["text"] == "2 reminders went off more than three days ago and are still open."
    assert {r["label"] for r in drop["receipts"]} == {"Call the landlord", "Renew the passport"}
    shift = by_kind["activity_shift"]
    assert shift["text"] == "You sent 9 messages in the last 7 days, more than the 5 the week before."
    assert [r["count"] for r in shift["receipts"]] == [9, 5] and shift["href"] == "/dashboard/chat"
    # Every sentence has at least one receipt, and every reminder receipt is one of this owner's own rows.
    with session_scope() as s:
        mine = {str(i) for i in s.scalars(select(Intention.id).where(Intention.owner_person_id == me))}
    for f in note["findings"]:
        assert f["text"] and f["receipts"] and f["href"].startswith("/dashboard/")
        for r in f["receipts"]:
            assert r["type"] == "count" or r["id"] in mine


def test_nothing_is_said_when_nothing_repeats(owner):
    me = person_id(OWNER["email"])
    with session_scope() as s:
        s.add(reminder(me, "Send the investor update", weeks_ago=0))
        s.add(reminder(me, "Send the investor update", weeks_ago=0))  # twice, but one week: not a pattern
    opted_in(owner)
    assert owner.get("/app/mirror").json()["note"] is None


def test_another_owners_rows_never_appear(browser, owner):
    seed(person_id(OWNER["email"]))
    b = browser()
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    opted_in(b)
    assert b.get("/app/mirror").json()["note"] is None
    opted_in(owner)
    assert "Priya" not in owner.get("/app/mirror").text


def test_the_note_is_made_once_a_week_and_makes_no_model_call(owner):
    seed(person_id(OWNER["email"]))
    opted_in(owner)
    a = owner.get("/app/mirror").json()["note"]
    b = owner.get("/app/mirror").json()["note"]
    assert a == b
    with session_scope() as s:
        assert len(s.scalars(select(MirrorNote)).all()) == 1
        assert s.scalars(select(SpendEntry)).all() == []  # no model was asked anything


def test_dont_mention_again_is_stored_and_honoured_next_week(owner):
    me = person_id(OWNER["email"])
    seed(me)
    opted_in(owner)
    note = owner.get("/app/mirror").json()["note"]
    idx = next(i for i, f in enumerate(note["findings"]) if f["kind"] == "repeated_reminders")
    r = owner.post(f"/app/mirror/notes/{note['id']}/findings/{idx}/feedback", json={"action": "mute"})
    assert r.status_code == 200
    assert owner.get("/app/mirror").json()["note"]["findings"][idx]["feedback"] == "mute"
    assert owner.get("/app/mirror").json()["muted"] == 1
    with session_scope() as s:
        later = service.this_weeks_note(s, me, now=NOW + timedelta(weeks=1))
        assert later is not None
        assert "repeated_reminders" not in {f["kind"] for f in later.findings}


def test_not_useful_twice_stops_that_kind(owner):
    me = person_id(OWNER["email"])
    seed(me)
    opted_in(owner)
    note = owner.get("/app/mirror").json()["note"]
    idx = next(i for i, f in enumerate(note["findings"]) if f["kind"] == "activity_shift")
    for _ in range(2):
        assert (
            owner.post(
                f"/app/mirror/notes/{note['id']}/findings/{idx}/feedback", json={"action": "not_useful"}
            ).status_code
            == 200
        )
    with session_scope() as s:
        later = service.this_weeks_note(s, me, now=NOW + timedelta(weeks=1))
        assert later is not None and "activity_shift" not in {f["kind"] for f in later.findings}


def test_feedback_is_checked(browser, owner):
    seed(person_id(OWNER["email"]))
    opted_in(owner)
    note = owner.get("/app/mirror").json()["note"]
    url = f"/app/mirror/notes/{note['id']}/findings/%s/feedback"
    assert owner.post(url % 99, json={"action": "mute"}).status_code == 404
    assert owner.post(url % 0, json={"action": "like"}).status_code == 422
    other = browser()
    assert signup(other, **OTHER).status_code == 200 and hire(other).status_code == 200
    opted_in(other)
    assert other.post(url % 0, json={"action": "mute"}).status_code == 404  # not their note


def test_turning_it_off_deletes_the_notes(owner):
    seed(person_id(OWNER["email"]))
    opted_in(owner)
    assert owner.get("/app/mirror").json()["note"] is not None
    assert owner.post("/app/mirror/settings", json={"enabled": False}).json() == {"enabled": False, "deleted": 1}
    with session_scope() as s:
        assert s.scalars(select(MirrorNote)).all() == []
    assert owner.get("/app/mirror").json()["note"] is None


def test_the_wording_makes_no_inference_about_feelings_health_or_other_people():
    """Every sentence comes from a template in patterns.py; none of them can mention mood, health or a person."""
    source = open(patterns.__file__).read()
    texts = re.findall(r'f?"(You [^"]*|\{len\(rows\)\} reminders[^"]*)"', source)
    assert texts, "templates found"
    banned = re.compile(r"stress|anxi|tired|mood|happy|sad|angry|sleep|health|\bill\b|burn|warm|cold|rude|upset", re.I)
    assert not any(banned.search(t) for t in texts), texts
