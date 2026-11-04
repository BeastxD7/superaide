"""Approval fatigue metrics (#407): counts and timings of cards, never what a card said or who it was for."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Integer, String, Text, select, text

from superaide.approvals import burden
from superaide.approvals.binding import EXPIRED_BY
from superaide.approvals.models import Approval, ApprovalEvent
from superaide.platform.db.session import session_scope

ADMIN = {"Authorization": "Bearer test-admin-token"}
RECIPIENT = "meera.iyer@example.com"
SECRET = "Quarterly layoffs list for Rahul Verma"


def card(kind: str = "gmail_send", *, age_s: float = 60, status: str = "pending", expires: float | None = None) -> str:
    with session_scope() as s:
        a = Approval(
            kind=kind,
            payload={"summary": f"{SECRET}", "to": RECIPIENT, "fields": [{"label": "To", "value": RECIPIENT}]},
            status=status,
        )
        s.add(a)
        s.flush()
        a.created_at = datetime.now(UTC) - timedelta(seconds=age_s)
        if expires is not None:
            a.expires_at = datetime.now(UTC) + timedelta(seconds=expires)
        return str(a.id)


def events(event: str | None = None) -> list[ApprovalEvent]:
    with session_scope() as s:
        q = select(ApprovalEvent).order_by(ApprovalEvent.created_at)
        if event:
            q = q.where(ApprovalEvent.event == event)
        found = list(s.scalars(q))
        s.expunge_all()
        return found


def set_event_age(event_id: uuid.UUID, seconds: float) -> None:
    with session_scope() as s:
        s.execute(
            text("UPDATE approval_event SET created_at = now() - make_interval(secs => :s) WHERE id = :i"),
            {"s": seconds, "i": event_id},
        )


# --- the events ---------------------------------------------------------------------------------------------------


def test_a_card_is_shown_once_however_often_the_list_is_read(owner):
    a = card()
    for _ in range(3):
        owner.get("/app/approvals")
    assert [(e.event, str(e.approval_id)) for e in events()] == [("shown", a)]


def test_an_expired_or_decided_card_is_not_counted_as_shown(owner):
    card(status="approved")
    card(expires=-5)
    owner.get("/app/approvals")
    assert events() == []


def test_opening_is_recorded_once_and_only_while_the_card_waits(owner):
    a = card()
    assert owner.post(f"/app/approvals/{a}/opened").json() == {"recorded": True}
    assert owner.post(f"/app/approvals/{a}/opened").json() == {"recorded": False}
    done = card(status="approved")
    assert owner.post(f"/app/approvals/{done}/opened").json() == {"recorded": False}
    assert owner.post(f"/app/approvals/{uuid.uuid4()}/opened").status_code == 404
    assert [e.event for e in events()] == ["opened"]


def test_a_decision_without_opening_is_a_rubber_stamp(owner):
    a = card(age_s=30)
    owner.get("/app/approvals")
    assert owner.post(f"/app/approvals/{a}/approve").status_code == 200
    [d] = events("decided")
    assert (d.verdict, d.via, d.opened, d.since_opened_ms) == ("approve", "app", False, None)
    assert 29_000 <= d.latency_ms <= 40_000


def test_a_decision_within_two_seconds_of_opening_is_a_rubber_stamp_and_a_slow_one_is_not(owner):
    fast, slow = card(age_s=100), card(age_s=100)
    owner.post(f"/app/approvals/{fast}/opened")
    owner.post(f"/app/approvals/{slow}/opened")
    [_, slow_open] = events("opened")
    set_event_age(slow_open.id, 20)  # opened 20 seconds ago
    owner.post(f"/app/approvals/{fast}/approve")
    owner.post(f"/app/approvals/{slow}/reject")
    by_id = {str(e.approval_id): e for e in events("decided")}
    assert by_id[fast].opened is True and by_id[fast].since_opened_ms < burden.QUICK_MS
    assert by_id[slow].opened is True and by_id[slow].since_opened_ms >= 19_000 and by_id[slow].verdict == "reject"


def test_the_same_card_cannot_be_decided_twice_in_the_metrics(owner):
    a = card()
    owner.post(f"/app/approvals/{a}/approve")
    assert owner.post(f"/app/approvals/{a}/approve").status_code == 409
    assert len(events("decided")) == 1


def test_a_yes_after_expiry_counts_as_expired_not_as_a_decision(owner):
    a = card(expires=-1)
    owner.post(f"/app/approvals/{a}/approve")
    with session_scope() as s:
        assert s.get(Approval, uuid.UUID(a)).decided_by == EXPIRED_BY
    [d] = events("decided")
    assert d.verdict == "expired"
    with session_scope() as s:
        assert burden.summary(s)["totals"]["decided"] == 0
        assert burden.summary(s)["totals"]["expired"] == 1


def test_the_brake_firing_is_counted_each_time(owner, monkeypatch, settings):
    from superaide.policy import brake

    monkeypatch.setattr(settings, "rubber_stamp_brake_enabled", True)
    for _ in range(10):
        card("publish_post", age_s=1, status="approved")
    with session_scope() as s:  # ten fast yeses: the brake is engaged
        for a in s.scalars(select(Approval)):
            a.decided_at = a.created_at + timedelta(seconds=1)
    held = card("publish_post")
    assert owner.post(f"/app/approvals/{held}/approve").status_code == 422
    assert owner.post(f"/app/approvals/{held}/approve", json={"reason": "short"}).status_code == 422
    assert brake.state is not None
    assert len(events("brake_held")) == 2 and not events("decided")


# --- the numbers --------------------------------------------------------------------------------------------------


def decided_event(kind: str, verdict: str, latency_s: float, *, opened: bool, since_opened_s: float | None = None,
                  weeks_ago: int = 0) -> None:  # fmt: skip
    with session_scope() as s:
        a = Approval(kind=kind, payload={}, status="approved" if verdict == "approve" else "rejected")
        s.add(a)
        s.flush()
        when = datetime.now(UTC) - timedelta(weeks=weeks_ago)
        a.created_at = when
        s.add(
            ApprovalEvent(
                approval_id=a.id, kind=kind, event="decided", verdict=verdict, via="app",
                latency_ms=round(latency_s * 1000), opened=opened,
                since_opened_ms=None if since_opened_s is None else round(since_opened_s * 1000),
                created_at=when,
            )
        )  # fmt: skip


def test_summary_has_every_metric_of_the_issue():
    for latency in (4, 10, 20, 30, 100):
        decided_event("gmail_send", "approve", latency, opened=True, since_opened_s=latency)
    decided_event("gmail_send", "approve", 50, opened=False)
    decided_event("grant_access", "reject", 8, opened=True, since_opened_s=1.0)
    decided_event("gmail_send", "approve", 5, opened=True, since_opened_s=1.5, weeks_ago=2)
    with session_scope() as s:
        out = burden.summary(s, weeks=4)
    t = out["totals"]
    assert t["requested_total"] == 8 and t["requested_by_kind"] == {"gmail_send": 7, "grant_access": 1}
    assert t["decided"] == 8 and t["approved"] == 7 and t["rejected"] == 1
    assert t["reject_rate"] == 0.125
    assert t["median_s"] == 15.0 and t["p90_s"] == 65.0  # 8 values: 4 5 8 10 20 30 50 100
    assert t["unopened"] == 1
    assert t["quick"] == 3  # the unopened one, the 1.0 s one and last fortnight's 1.5 s one
    assert t["rubber_stamp_share"] == 0.375
    assert t["per_week"] == 2.0
    this_week, *_, oldest = out["weeks"]
    assert this_week["week"] > oldest["week"]  # newest first
    assert this_week["decided"] == 7 and oldest["decided"] == 1


def test_summary_counts_expired_requests_and_nothing_to_divide_is_none():
    card(expires=-60)  # still waiting, past its expiry
    card(expires=3600)  # waiting, fine
    with session_scope() as s:
        t = burden.summary(s)["totals"]
    assert t["expired"] == 1 and t["requested_total"] == 2 and t["decided"] == 0
    assert t["median_s"] is None and t["rubber_stamp_share"] is None and t["reject_rate"] is None


def test_only_the_window_is_read():
    decided_event("gmail_send", "approve", 5, opened=False, weeks_ago=40)
    with session_scope() as s:
        assert burden.summary(s, weeks=8)["totals"]["decided"] == 0


# --- the routes ---------------------------------------------------------------------------------------------------


def test_the_owner_card_route(owner):
    decided_event("gmail_send", "approve", 12, opened=True, since_opened_s=9)
    body = owner.get("/app/approvals/burden").json()
    assert body["totals"]["decided"] == 1 and body["window_weeks"] == 8 and body["quick_ms"] == 2000
    assert body["brake_enabled"] is False
    assert owner.get("/app/approvals/burden?weeks=0").status_code == 422
    assert owner.get("/app/approvals/burden?weeks=27").status_code == 422


def test_the_operator_route(owner, app):
    decided_event("gmail_send", "reject", 12, opened=True, since_opened_s=9)
    client = TestClient(app)
    body = client.get("/api/approval-metrics", headers=ADMIN).json()
    body = body.get("data", body)
    assert body["totals"]["rejected"] == 1 and body["weeks"][0]["reject_rate"] == 1.0
    assert client.get("/api/approval-metrics").status_code in (401, 403)
    assert client.get("/api/approval-metrics?weeks=99", headers=ADMIN).status_code == 422


def test_off_means_nothing_is_recorded_and_the_routes_answer_404(owner, app, monkeypatch, settings):
    monkeypatch.setattr(settings, "approval_metrics_enabled", False)
    a = card()
    owner.get("/app/approvals")
    assert owner.post(f"/app/approvals/{a}/opened").status_code == 404
    assert owner.post(f"/app/approvals/{a}/approve").status_code == 200  # the approval itself is untouched
    assert events() == []
    assert owner.get("/app/approvals/burden").status_code == 404
    assert TestClient(app).get("/api/approval-metrics", headers=ADMIN).status_code == 404


# --- privacy and scale --------------------------------------------------------------------------------------------


def test_no_card_text_recipient_or_name_is_stored(owner):
    a = card()
    owner.get("/app/approvals")
    owner.post(f"/app/approvals/{a}/opened")
    owner.post(f"/app/approvals/{a}/approve")
    rows = events()
    assert {e.event for e in events()} == {"shown", "opened", "decided"}
    dumped = json.dumps([{c.name: str(getattr(e, c.name)) for c in ApprovalEvent.__table__.columns} for e in rows])
    for secret in (RECIPIENT, "meera", "layoffs", "Rahul", "example.com"):
        assert secret not in dumped
    with session_scope() as s:
        summary = json.dumps(burden.summary(s))
    for secret in (RECIPIENT, "layoffs", "Rahul"):
        assert secret not in summary


def test_the_table_has_only_words_numbers_and_flags():
    kinds = {c.name: type(c.type) for c in ApprovalEvent.__table__.columns}
    assert Text not in kinds.values()
    strings = {c.name: c.type.length for c in ApprovalEvent.__table__.columns if isinstance(c.type, String)}
    assert set(strings) == {"kind", "event", "verdict", "via"} and max(strings.values()) <= 40
    assert Integer in kinds.values() and Boolean in kinds.values()


def test_the_weekly_statements_use_the_index_at_scale():
    """5,000 events over two years, an 8 week window: EXPLAIN must show the (agent_id, created_at) index."""
    with session_scope() as s:
        s.execute(
            text(
                """
                INSERT INTO approval_event (id, tenant_id, agent_id, created_at, approval_id, kind, event, via)
                SELECT gen_random_uuid(), CAST(:t AS uuid), CAST(:a AS uuid), now() - g * interval '3 hours',
                       gen_random_uuid(), 'gmail_send', 'shown', ''
                FROM generate_series(1, 5000) g
                """
            ),
            {"t": str(_scope()[0]), "a": str(_scope()[1])},
        )
        s.execute(text("ANALYZE approval_event"))
        stmts = burden.weekly_statements(datetime.now(UTC) - timedelta(weeks=1))
        plan = "\n".join(
            r[0]
            for r in s.execute(
                text("EXPLAIN " + str(stmts["events"].compile(s.get_bind(), compile_kwargs={"literal_binds": True})))
            )
        )
    assert "ix_approval_event_agent_created" in plan or "ix_approval_event_agent_id" in plan, plan
    assert "Seq Scan on approval_event" not in plan, plan


def _scope() -> tuple[uuid.UUID, uuid.UUID]:
    from superaide.platform.tenancy.context import default_scope

    sc = default_scope()
    return sc.tenant_id, sc.agent_id
