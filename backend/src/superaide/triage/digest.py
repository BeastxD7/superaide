"""Triage in the digest (ADR 0010 TR8): what needs the owner, what escalated and whether it was
acknowledged, what Ava handled, what she took a message for, and counts for the rest. Built in
code from `triage_decision` rows since the last digest; no model call.

Also the catch-up after quiet hours: when anything was decided while the owner was in quiet
hours, one short note is sent as they end.

Behind TRIAGE_ENABLED: with it off, `digest_lines` returns nothing without touching the database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Event, Job, Person
from superaide.jobs.queue import enqueue, handler
from superaide.triage.models import Escalation, TriageDecision
from superaide.triage.types import Decision

JOB_CATCHUP = "triage_catchup"
QUOTE_HIDDEN = {"payment", "legal_hr", "credentials"}  # the digest may be pushed to a chat: never quote these
FALLBACKS = ("classifier:bad_quote", "classifier:error", "classifier:budget", "classifier:unparsed")
MAX_ITEMS = 8


def enabled() -> bool:
    from superaide.config import get_settings

    return get_settings().triage_enabled


def last_digest_at(session: Session, owner: Person, now: datetime) -> datetime:
    """When the owner last got a digest in the app (or a day ago)."""
    from superaide.app.accounts import web_message
    from superaide.memory.store import get_or_create_context

    ctx = get_or_create_context(session, web_message(owner, ""))
    at = session.scalar(
        select(Event.created_at)
        .where(
            Event.context_id == ctx.id,
            Event.direction == "out",
            Event.meta["kind"].astext == "digest",
            Event.created_at < now,
        )  # fmt: skip
        .order_by(Event.created_at.desc())
        .limit(1)
    )
    return at or now - timedelta(hours=24)


def _who(session: Session, d: TriageDecision) -> str:
    p = session.get(Person, d.sender_person_id) if d.sender_person_id else None
    return p.display_name if p else "Someone new"


def _quote(d: TriageDecision) -> str:
    c = d.classifier or {}
    if set(c.get("risk_flags") or []) & QUOTE_HIDDEN:
        return ""
    ev = list(c.get("evidence") or [])
    return f" — “{ev[0][:120]}”" if ev else ""


def sections(session: Session, owner: Person, since: datetime, now: datetime) -> list[str]:
    rows = session.scalars(
        select(TriageDecision)
        .where(
            TriageDecision.agent_id == owner.agent_id,
            TriageDecision.created_at >= since,
            TriageDecision.created_at < now,
        )  # fmt: skip
        .order_by(TriageDecision.created_at)
    ).all()
    if not rows:
        return []
    email = [d for d in rows if (d.features or {}).get("provider") == "email"]
    rows = [d for d in rows if (d.features or {}).get("provider") != "email"]
    from superaide.skills.gmail_inbox import digest_section

    mail_lines = digest_section(session, email)  # counts and top items; never quotes money, legal or codes
    if not rows:
        return [*mail_lines, _SOONER] if mail_lines else []
    escs = {
        e.decision_id: e
        for e in session.scalars(select(Escalation).where(Escalation.decision_id.in_([d.id for d in rows])))
    }
    decide, escalated, handled, taken = [], [], [], []
    fyi = low = 0
    for d in rows:
        maybe = bool((d.features or {}).get("maybe_urgent"))
        if any(h in d.rule_hits for h in FALLBACKS):
            low += 1
        if (
            d.decision == Decision.VERIFY
            or (d.decision == Decision.HOLD and maybe)
            or (d.decision == Decision.HANDLE and (d.features or {}).get("needs_approval"))
        ):
            tag = "unverified request" if d.decision == Decision.VERIFY else "maybe urgent"
            if d.decision == Decision.HANDLE:
                tag = "a draft to approve"
            if d.feedback is None:
                decide.append(f"- {_who(session, d)} ({tag}): {d.reason}")
        elif d.decision in (Decision.INTERRUPT, Decision.DEFER):
            esc = escs.get(d.id)
            state = (
                "acknowledged"
                if esc and esc.acked_at
                else ("not acknowledged" if esc and esc.state == "expired" else "waiting" if esc else "")
            )
            escalated.append(f"- {_who(session, d)}: {d.reason} ({state})")
        elif d.decision == Decision.HANDLE:
            handled.append(f"- {_who(session, d)}: {d.reason}")
        elif any(h.startswith(("R2_", "R4_")) for h in d.rule_hits):
            fyi += 1
        else:
            taken.append(f"- {_who(session, d)}: {d.reason}{_quote(d)}")

    lines: list[str] = []
    for title, items in (
        ("**Needs your decision**", decide),
        ("**Escalations**", escalated),
        ("**Handled for you**", handled),
        ("**Messages taken**", taken),
    ):
        if items:
            lines.append(title)
            lines += items[:MAX_ITEMS]
            if len(items) > MAX_ITEMS:
                lines.append(f"- and {len(items) - MAX_ITEMS} more in Needs you")
    if fyi:
        lines.append(f"**FYI:** {fyi} from automated or silenced senders")
    if low:
        lines.append(f"**Couldn't sort:** {low}, held for you to look at")
    lines += mail_lines
    if lines:
        lines.append(_SOONER)
    return lines


_SOONER = "_Anything I should have told you sooner? Mark it in Needs you._"


def digest_lines(session: Session, owner: Person, now: datetime) -> list[str]:
    """For proactive/digest.py: the triage sections since the last digest. [] while triage is off."""
    if not enabled():
        return []
    return sections(session, owner, last_digest_at(session, owner, now), now)


# --- the catch-up after quiet hours ----------------------------------------------------------


def schedule_catchup(session: Session, owner: Person, quiet_end: datetime) -> None:
    """Once per quiet period: a short note when it ends, if anything came in during it."""
    enqueue(
        session, JOB_CATCHUP, {"owner_id": str(owner.id), "since": (quiet_end - timedelta(hours=24)).isoformat()},
        run_at=quiet_end, dedupe_key=f"triage-catchup:{owner.id}:{quiet_end.isoformat()}",
    )  # fmt: skip


@handler(JOB_CATCHUP)
def _catchup(session: Session, job: Job) -> None:
    from superaide.knowledge.store import preferences_for
    from superaide.proactive.notify import notify_owner
    from superaide.triage.features import next_local

    owner = session.get(Person, uuid.UUID(job.payload["owner_id"]))
    if owner is None:
        return
    now = max(datetime.now(UTC), job.run_at)
    pref = preferences_for(session, owner)
    # Everything since quiet hours began (the evening before), and not already in a digest.
    began = next_local(now - timedelta(hours=24), pref.timezone, pref.quiet_start)
    since = max(began, last_digest_at(session, owner, now))
    lines = sections(session, owner, since, now)
    if not lines:
        audit(session, "triage", "catchup_skipped_empty")
        return
    first = owner.display_name.split()[0]
    text = f"While your notifications were quiet, {first}:\n\n" + "\n".join(lines)
    via = notify_owner(session, owner, text, {"kind": "triage_catchup"})
    audit(session, "triage", "catchup_sent", via=via)
