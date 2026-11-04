"""Approval fatigue, measured (#407): how often the owner is asked, how fast they answer, whether they read first.

Only counts and timings are kept (`approval_event`, plus the `approval` rows that already exist): never the text
of a card, a recipient or a name, the same rule as the Principal Card counters. Nothing here changes how an approval
behaves; the rubber-stamp brake (policy/brake.py) keeps its own rule and is only counted when it fires.

Definitions the screens quote:
* requested: approval rows created in the week, by kind.
* decided: yes or no from the owner (a yes that arrived after the card expired counts as expired, not decided).
* rubber-stamped: decided without the card having been opened, or within QUICK_MS of opening it.
* time to decide: from the request being made to the answer, median and 90th percentile.
* expired: requests that ran out of time unanswered (still waiting past their expiry, or answered too late).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.approvals.binding import EXPIRED_BY
from superaide.approvals.models import Approval, ApprovalEvent
from superaide.platform.tenancy.context import scope_or_default

log = logging.getLogger(__name__)

QUICK_MS = 2000  # decided within this long of opening the card: not read, only clicked
MAX_WEEKS = 26
DEFAULT_WEEKS = 8


def _add(session: Session, approval: Approval, event: str, **fields: Any) -> bool:
    """Append one event; a repeat of a once-only event (shown, opened, decided) is quietly ignored."""
    stmt = (
        insert(ApprovalEvent)
        .values(
            id=uuid.uuid4(),
            tenant_id=approval.tenant_id,
            agent_id=approval.agent_id,
            created_at=datetime.now(UTC),
            approval_id=approval.id,
            kind=approval.kind[:40],
            event=event,
            **fields,
        )
        .on_conflict_do_nothing()
        .returning(ApprovalEvent.id)
    )
    return session.execute(stmt).first() is not None


def record_shown(session: Session, approvals: list[Approval]) -> None:
    """Called each time the list is read: only the first listing of a card is kept."""
    for a in approvals:
        _add(session, a, "shown")


def record_opened(session: Session, approval: Approval) -> bool:
    return _add(session, approval, "opened")


def record_brake_held(session: Session, approval: Approval) -> None:
    _add(session, approval, "brake_held")


def record_decided(session: Session, approval: Approval, *, verdict: str, via: str) -> None:
    """The owner's answer. Never raises: a metric must not undo a decision."""
    try:
        decided_at = approval.decided_at or datetime.now(UTC)
        opened_at = session.scalar(
            select(ApprovalEvent.created_at).where(
                ApprovalEvent.approval_id == approval.id, ApprovalEvent.event == "opened"
            )
        )
        with session.begin_nested():
            _add(
                session,
                approval,
                "decided",
                verdict=verdict,
                via=(via or "app")[:16],
                latency_ms=max(0, round((decided_at - approval.created_at).total_seconds() * 1000)),
                opened=opened_at is not None,
                since_opened_ms=None
                if opened_at is None
                else max(0, round((decided_at - opened_at).total_seconds() * 1000)),
            )
    except Exception:
        log.warning("couldn't record an approval decision metric", exc_info=True)


def _week(column: Any) -> Any:
    return func.date_trunc("week", column)


def weekly_statements(since: datetime) -> dict[str, Any]:
    """The three statements behind `summary`, each bounded by `created_at >= since` so the (agent_id, created_at)
    indexes answer them. Exposed so a test can EXPLAIN exactly what runs."""
    ev = ApprovalEvent
    agent = scope_or_default().agent_id  # named in the query, not only by row-level security, so the index leads
    quick = (ev.opened.is_(False)) | (ev.since_opened_ms <= QUICK_MS)
    return {
        "requested": select(_week(Approval.created_at).label("week"), Approval.kind, func.count().label("n"))
        .where(Approval.agent_id == agent, Approval.created_at >= since)
        .group_by("week", Approval.kind),
        "expired": select(_week(Approval.created_at).label("week"), func.count().label("n"))
        .where(
            Approval.agent_id == agent,
            Approval.created_at >= since,
            ((Approval.status == "pending") & (Approval.expires_at < func.now())) | (Approval.decided_by == EXPIRED_BY),
        )
        .group_by("week"),
        "events": select(
            _week(ev.created_at).label("week"),
            func.count().filter(ev.event == "decided", ev.verdict.in_(("approve", "reject"))).label("decided"),
            func.count().filter(ev.event == "decided", ev.verdict == "approve").label("approved"),
            func.count().filter(ev.event == "decided", ev.verdict == "reject").label("rejected"),
            func.count().filter(ev.event == "decided", ev.verdict.in_(("approve", "reject")), quick).label("quick"),
            func.count()
            .filter(ev.event == "decided", ev.verdict.in_(("approve", "reject")), ev.opened.is_(False))
            .label("unopened"),
            func.count().filter(ev.event == "brake_held").label("brake_held"),
            func.percentile_cont(0.5)
            .within_group(cast(ev.latency_ms, Float))
            .filter(ev.event == "decided", ev.verdict.in_(("approve", "reject")))
            .label("p50"),
            func.percentile_cont(0.9)
            .within_group(cast(ev.latency_ms, Float))
            .filter(ev.event == "decided", ev.verdict.in_(("approve", "reject")))
            .label("p90"),
        )
        .where(ev.agent_id == agent, ev.created_at >= since)
        .group_by("week"),
    }


def _since(weeks: int) -> datetime:
    start = datetime.now(UTC) - timedelta(weeks=weeks)
    return start - timedelta(
        days=start.weekday(),
        hours=start.hour,
        minutes=start.minute,
        seconds=start.second,
        microseconds=start.microsecond,
    )


def _s(ms: float | None) -> float | None:
    return None if ms is None else round(float(ms) / 1000, 1)


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 3)


def summary(session: Session, *, weeks: int = DEFAULT_WEEKS) -> dict[str, Any]:
    """Counts and timings per week (newest first) and for the whole window. Numbers only."""
    since = _since(weeks)
    stmts = weekly_statements(since)
    buckets: dict[str, dict[str, Any]] = {}

    def bucket(week: datetime) -> dict[str, Any]:
        key = week.date().isoformat()
        return buckets.setdefault(
            key,
            {"week": key, "requested": {}, "requested_total": 0, "expired": 0, "decided": 0, "approved": 0,
             "rejected": 0, "quick": 0, "unopened": 0, "brake_held": 0, "median_s": None, "p90_s": None},
        )  # fmt: skip

    for r in session.execute(stmts["requested"]):
        b = bucket(r.week)
        b["requested"][r.kind] = int(r.n)
        b["requested_total"] += int(r.n)
    for r in session.execute(stmts["expired"]):
        bucket(r.week)["expired"] = int(r.n)
    for r in session.execute(stmts["events"]):
        b = bucket(r.week)
        b.update(decided=int(r.decided), approved=int(r.approved), rejected=int(r.rejected), quick=int(r.quick))
        b.update(unopened=int(r.unopened), brake_held=int(r.brake_held), median_s=_s(r.p50), p90_s=_s(r.p90))
    weeks_out = sorted(buckets.values(), key=lambda b: b["week"], reverse=True)
    for b in weeks_out:
        b["rubber_stamp_share"] = _share(b["quick"], b["decided"])
        b["reject_rate"] = _share(b["rejected"], b["decided"])
    totals = {
        k: sum(b[k] for b in weeks_out)
        for k in ("requested_total", "expired", "decided", "approved", "rejected", "quick", "unopened", "brake_held")
    }
    by_kind: dict[str, int] = {}
    for b in weeks_out:
        for kind, n in b["requested"].items():
            by_kind[kind] = by_kind.get(kind, 0) + n
    overall = session.execute(
        select(
            func.percentile_cont(0.5).within_group(cast(ApprovalEvent.latency_ms, Float)),
            func.percentile_cont(0.9).within_group(cast(ApprovalEvent.latency_ms, Float)),
        ).where(
            ApprovalEvent.agent_id == scope_or_default().agent_id,
            ApprovalEvent.created_at >= since,
            ApprovalEvent.event == "decided",
            ApprovalEvent.verdict.in_(("approve", "reject")),
        )
    ).one()
    totals.update(
        requested_by_kind=by_kind,
        median_s=_s(overall[0]),
        p90_s=_s(overall[1]),
        rubber_stamp_share=_share(totals["quick"], totals["decided"]),
        reject_rate=_share(totals["rejected"], totals["decided"]),
        per_week=round(totals["requested_total"] / weeks, 1),
    )
    return {"window_weeks": weeks, "weeks": weeks_out, "totals": totals, "quick_ms": QUICK_MS}
