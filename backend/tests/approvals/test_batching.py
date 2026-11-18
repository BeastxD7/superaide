"""Batch approval (#408): alike, safe own-calendar requests as one card; a changed item cancels the batch; the
never-batch list is enforced in code; mandate suggestions appear only after the threshold and are narrow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.approvals import batching
from superaide.approvals.binding import seal
from superaide.approvals.models import Approval
from superaide.mandate import service, suggest
from superaide.mandate import token as tk
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope

H = {"X-Requested-With": "superaide"}


@pytest.fixture(autouse=True)
def keyed(monkeypatch, settings):
    monkeypatch.setattr(service, "root_key", lambda: b"k" * 32)
    monkeypatch.setattr(settings, "mandates_enabled", True)


def card(
    kind="calendar_create_event", *, tainted=False, extra=None, status="pending", expires=None, decided=None
) -> str:
    payload = {"summary": "Gym tomorrow 7am", "event": {"summary": "Gym"}, "notify": False, "tainted": tainted}
    payload.update(extra or {})
    with session_scope() as s:
        a = Approval(kind=kind, payload=payload, status=status)
        seal(a)
        if expires is not None:
            a.expires_at = datetime.now(UTC) + timedelta(seconds=expires)
        if decided:
            a.decided_at = decided
        s.add(a)
        s.flush()
        return str(a.id)


def batches(owner):
    r = owner.get("/app/approvals/batches")
    assert r.status_code == 200, r.text
    return r.json()


def decide(owner, verdict, ids, digest):
    return owner.post(f"/app/approvals/batch/{verdict}", json={"ids": ids, "digest": digest}, headers=H)


def status_of(i: str) -> str:
    with session_scope() as s:
        return s.execute(select(Approval.status).where(Approval.id == i)).scalar_one()


def test_two_alike_own_calendar_requests_form_one_group_with_every_item(owner):
    a, b = card(), card()
    [g] = batches(owner)
    assert g["kind"] == "calendar_create_event"
    assert {i["id"] for i in g["items"]} == {a, b}
    assert all(i["summary"] for i in g["items"])


def test_a_single_request_is_not_a_batch(owner):
    card()
    assert batches(owner) == []


def test_approving_the_batch_resolves_each_item_on_its_own(owner):
    ids = [card(), card(), card()]
    [g] = batches(owner)
    r = decide(owner, "approve", ids, g["digest"])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["approved"] * 3
    assert all(status_of(i) == "approved" for i in ids)
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "approval_batch_decided")) is not None


def test_rejecting_the_batch_rejects_every_item(owner):
    ids = [card(), card()]
    [g] = batches(owner)
    assert decide(owner, "reject", ids, g["digest"]).status_code == 200
    assert all(status_of(i) == "rejected" for i in ids)


def test_a_changed_item_invalidates_the_whole_batch(owner):
    a, b = card(), card()
    [g] = batches(owner)
    with session_scope() as s:  # someone edits one item after the owner was shown the batch
        row = s.get(Approval, b)
        row.payload = {**row.payload, "event": {"summary": "Gym, and invite the whole team"}}
    r = decide(owner, "approve", [a, b], g["digest"])
    assert r.status_code == 409
    assert status_of(a) == status_of(b) == "pending"  # nothing was decided, not even the untouched one
    assert batches(owner) == []  # and the edited one no longer qualifies


def test_an_added_item_invalidates_the_digest(owner):
    a, b = card(), card()
    [g] = batches(owner)
    c = card()
    assert decide(owner, "approve", [a, b, c], g["digest"]).status_code == 409
    assert all(status_of(i) == "pending" for i in (a, b, c))


def test_a_wrong_digest_decides_nothing(owner):
    ids = [card(), card()]
    assert decide(owner, "approve", ids, "0" * 64).status_code == 409
    assert all(status_of(i) == "pending" for i in ids)


@pytest.mark.parametrize(
    "extra",
    [
        {"notify": True},
        {"event": {"summary": "Sync", "attendees": [{"email": "rahul.verma@example.com"}]}},
        {"origin": "voice"},
        {"warnings": ["a person not seen before"]},
        {"tainted": True},
    ],
)
def test_risky_items_are_never_grouped(owner, extra):
    card()
    card(extra=extra)
    assert batches(owner) == []


def test_a_row_that_does_not_say_whether_it_was_tainted_is_left_out(owner):
    card()
    with session_scope() as s:
        a = Approval(kind="calendar_create_event", payload={"summary": "Old row"})
        seal(a)
        s.add(a)
    assert batches(owner) == []


def test_an_unsealed_or_mismatched_item_is_left_out(owner):
    card()
    b = card()
    with session_scope() as s:
        s.get(Approval, b).bound_hash = None
    assert batches(owner) == []


def test_an_expired_item_is_left_out(owner):
    card()
    card(expires=-5)
    assert batches(owner) == []


@pytest.mark.parametrize("kind", sorted(batching.NEVER_BATCH | {"gmail_send", "calendar_decline_event"}))
def test_the_never_batch_kinds_are_never_grouped_or_decided_together(owner, kind):
    ids = [card(kind), card(kind)]
    assert batches(owner) == []
    with session_scope() as s:
        digest = batching.digest_of([s.get(Approval, i) for i in ids])
    assert decide(owner, "approve", ids, digest).status_code == 409
    assert all(status_of(i) == "pending" for i in ids)


def test_the_hard_list_and_the_allow_list_do_not_overlap():
    assert not (set(batching.BATCHABLE) & batching.NEVER_BATCH)
    assert {"gmail_send", "calendar_decline_event", "grant_access", "delete_memory", "message_someone_else"} <= (
        batching.NEVER_BATCH
    )


def test_different_kinds_cannot_be_decided_in_one_batch(owner):
    a, b = card("calendar_create_event"), card("calendar_move_event")
    with session_scope() as s:
        digest = batching.digest_of([s.get(Approval, a), s.get(Approval, b)])
    assert decide(owner, "approve", [a, b], digest).status_code == 409


def test_duplicate_ids_are_refused(owner):
    a = card()
    assert decide(owner, "approve", [a, a], "0" * 64).status_code == 422


def test_batching_off_means_no_groups_and_404(owner, settings, monkeypatch):
    ids = [card(), card()]
    [g] = batches(owner)
    monkeypatch.setattr(settings, "approval_batch_max", 0)
    assert owner.get("/app/approvals/batches").status_code == 404
    assert decide(owner, "approve", ids, g["digest"]).status_code == 404
    assert all(status_of(i) == "pending" for i in ids)
    # the ordinary one-by-one route still works
    assert owner.post(f"/app/approvals/{ids[0]}/approve", json={}, headers=H).status_code == 200


def test_an_engaged_brake_blocks_a_batch_yes_but_not_a_batch_no(owner, settings, monkeypatch):
    from superaide.policy import brake

    monkeypatch.setattr(brake, "state", lambda s: brake.BrakeState(True, 20, 1.0, 0.0, {}))
    monkeypatch.setattr(settings, "rubber_stamp_brake_enabled", True)
    ids = [card(), card()]
    [g] = batches(owner)
    assert decide(owner, "approve", ids, g["digest"]).status_code == 422
    assert all(status_of(i) == "pending" for i in ids)
    assert decide(owner, "reject", ids, g["digest"]).status_code == 200


def test_a_stranger_and_a_signed_out_visitor_cannot_use_the_batch_routes(other, browser):
    for c in (other, browser()):
        assert c.get("/app/approvals/batches").status_code in (401, 403)
        r = c.post("/app/approvals/batch/approve", json={"ids": [], "digest": "0" * 64}, headers=H)
        assert r.status_code in (401, 403, 422)


# --- mandate suggestions ---------------------------------------------------------------------------------------


def approved(n: int, kind="calendar_create_event", **kw):
    for _ in range(n):
        card(kind, status="approved", decided=datetime.now(UTC), **kw)


def test_no_offer_before_the_threshold_and_one_after(owner):
    approved(4)
    assert owner.get("/app/mandates/suggestions").json() == {"suggestions": []}
    approved(1)
    [offer] = owner.get("/app/mandates/suggestions").json()["suggestions"]
    assert offer["kind"] == "calendar_create_event" and offer["count"] == 5


def test_tainted_or_guest_requests_do_not_count_toward_an_offer(owner):
    approved(3)
    approved(2, tainted=True)
    approved(2, extra={"notify": True})
    assert owner.get("/app/mandates/suggestions").json()["suggestions"] == []


def test_kinds_that_tell_others_are_never_offered(owner):
    approved(9, kind="gmail_send")
    approved(9, kind="calendar_decline_event")
    assert owner.get("/app/mandates/suggestions").json()["suggestions"] == []


def test_accepting_signs_a_narrow_revocable_mandate_and_the_offer_goes_away(owner):
    approved(5)
    r = owner.post("/app/mandates/suggestions/calendar_create_event/accept", headers=H)
    assert r.status_code == 200, r.text
    with session_scope() as s:
        from superaide.mandate.models import Mandate

        [m] = s.scalars(select(Mandate)).all()
        eff = tk.effective(tk.loads(m.token))
        assert eff["tools"] == ["create_event"] and eff["max_uses"] == suggest.MAX_USES
        assert m.expires_at <= datetime.now(UTC) + timedelta(days=suggest.VALID_DAYS, minutes=1)
    assert owner.get("/app/mandates/suggestions").json()["suggestions"] == []
    assert owner.post(f"/app/mandates/{r.json()['id']}/revoke", headers=H).status_code == 200


def test_accepting_without_an_offer_or_for_a_kind_that_is_not_suggestable_fails(owner):
    approved(2)
    for kind in ("calendar_create_event", "gmail_send", "grant_access"):
        assert owner.post(f"/app/mandates/suggestions/{kind}/accept", headers=H).status_code == 404


def test_dismissing_hides_the_offer(owner):
    approved(5)
    assert owner.post("/app/mandates/suggestions/calendar_create_event/dismiss", headers=H).status_code == 200
    assert owner.get("/app/mandates/suggestions").json()["suggestions"] == []


def test_suggestions_off_means_none(owner, settings, monkeypatch):
    approved(5)
    monkeypatch.setattr(settings, "mandate_suggest_after", 0)
    assert owner.get("/app/mandates/suggestions").json()["suggestions"] == []
