"""Gmail inbox triage: new inbox mail goes through the same triage as messages (ADR 0010).

A per-instance job (`gmail_poll`) reads what arrived since the last look with Gmail's
history.list every GMAIL_POLL_MINUTES (push via watch + Pub/Sub is out of scope), behind
GMAIL_TRIAGE_ENABLED and TRIAGE_ENABLED, both off by default. The first run only notes where
the inbox is now: older mail is never triaged.

Each new email becomes an inbound `Event` in an "email" `Context` (one per Gmail thread):
- its content is the subject and the start of the message's own text (cleaned and redacted,
  see connectors/gmail.py), and its audience is empty, so recall never shows it to anyone and
  it can't reach a prompt except through the Gmail tools, which taint the turn;
- the sender is a known person only through an email identity the owner attached
  (`POST /app/gmail/addresses`), and counts as verified only when Gmail says DMARC passed
  for their domain. An unchecked From line is just text (trust HINT), so it can never make
  someone a VIP;
- newsletters, lists and no-reply senders are automated (R4: held, FYI);
- the classifier (a model call) runs only for a verified, known sender; everything else is
  decided by the rules or held, so unknown senders cost nothing.

The results are `triage_decision` rows like any other: the Needs-you inbox, escalations and
the digest's email section (`digest_lines`) read them.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.connectors import gmail as api
from superaide.connectors import google
from superaide.db.models import ChannelIdentity, Context, Event, Job, Person
from superaide.identity.trust import Trust
from superaide.jobs.queue import enqueue, handler
from superaide.policy.killswitch import get_state, set_state
from superaide.skills import gmail
from superaide.triage.models import TriageDecision

JOB_KIND = "gmail_poll"
STATE_KEY = "gmail_sync"
MAX_PER_POLL = 50
EXCERPT = 1000  # characters of a message's own text that triage reads
# Mail the digest never quotes, whatever triage decided (it may be pushed to a chat).
NEVER_QUOTED = re.compile(
    r"\b(?:invoice|payment|bank|wire|iban|refund|card|password|passcode|verification|security code|login|"
    r"sign[- ]in|2fa|otp|lawsuit|legal|subpoena|court|attorney|solicitor|contract|termination|disciplinary|"
    r"grievance|tax)\b",
    re.I,
)
log = logging.getLogger(__name__)


def enabled() -> bool:
    from superaide.config import get_settings

    s = get_settings()
    return bool(s.gmail_triage_enabled and s.triage_enabled)


def _next_slot(now: datetime) -> datetime:
    from superaide.config import get_settings

    every = max(1, get_settings().gmail_poll_minutes) * 60
    return datetime.fromtimestamp((int(now.timestamp()) // every + 1) * every, UTC)


def ensure_scheduled(session: Session, now: datetime | None = None) -> None:
    """Queue the next poll for the instance in scope, if Gmail triage applies to it."""
    if not enabled() or not gmail.can_read(session):
        return
    at = _next_slot(now or datetime.now(UTC))
    enqueue(session, JOB_KIND, {}, run_at=at, dedupe_key=f"gmail-poll:{at.isoformat()}", max_attempts=1)


# --- one email in ----------------------------------------------------------------------------


def _identity(session: Session, address: str) -> ChannelIdentity | None:
    return session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.provider == "email",
            ChannelIdentity.stable_key == address,
            ChannelIdentity.lifecycle == "active",
            ChannelIdentity.person_id.is_not(None),
        )
    )


def _context(session: Session, thread_id: str, subject: str) -> Context:
    key = f"gmail:{thread_id}"[:200]
    ctx = session.scalar(
        select(Context).where(Context.provider == "email", Context.tenant_scope == "", Context.external_id == key)
    )
    if ctx is None:
        ctx = Context(provider="email", tenant_scope="", external_id=key, kind="dm", audience_class="private_1to1",
                      title=subject[:200])  # fmt: skip
        session.add(ctx)
        session.flush()
    return ctx


def ingest(session: Session, owner: Person, msg: dict[str, Any]) -> Event | None:
    """Log one inbox email as an inbound event (once). None when it's the owner's own, or seen before."""
    name, address = api.sender(msg)
    if not address or address == gmail.address(session):
        return None
    thread_id = str(msg.get("threadId") or msg.get("id"))
    subject = api.subject(msg)
    ctx = _context(session, thread_id, subject)
    mid = str(msg.get("id"))[:120]
    if session.scalar(select(Event.id).where(Event.context_id == ctx.id, Event.direction == "in",
                                             Event.external_id == mid)):  # fmt: skip
        return None
    verified = api.authenticated(msg)
    identity = _identity(session, address)
    trust = int(identity.trust_level) if identity is not None and verified else int(Trust.HINT)
    text = f"{subject}\n\n{api.clean_body(api.body_text(msg), EXCERPT)}".strip()
    event = Event(
        context_id=ctx.id, direction="in", sender_identity_id=identity.id if identity else None,
        resolved_person_id=identity.person_id if identity else None, trust_at_time=trust,
        audience_person_ids=[],  # never recalled into any prompt: email reaches the model only via its tools
        audience_has_unknown=False, content=text, external_id=mid, thread_id=thread_id[:120],
        webhook_verified=False,
        meta={"source": "gmail", "gmail_id": mid, "thread_id": thread_id, "from_name": name or api.domain(address),
              "from_domain": api.domain(address), "subject": subject, "verified": verified,
              "bulk": api.bulk(msg), "untrusted": True},
    )  # fmt: skip
    session.add(event)
    session.flush()
    return event


def triage(session: Session, owner: Person, event: Event, *, llm: Any, model: str, ready: bool,
           now: datetime | None = None) -> TriageDecision | None:  # fmt: skip
    """Decide about one logged email with the shared triage (rules → classifier → policy)."""
    from superaide.triage.features import gather
    from superaide.triage.service import decide

    existing = session.scalar(select(TriageDecision).where(TriageDecision.inbound_event_id == event.id))
    if existing is not None:
        return existing
    meta = event.meta or {}
    sender = session.get(Person, event.resolved_person_id) if event.resolved_person_id else None
    f = gather(session, event=event, owner=owner, now=now)
    verified = event.trust_at_time >= int(Trust.LINKED)
    f = dataclasses.replace(
        f,
        trust=event.trust_at_time,
        vip=bool(f.extra.get("vip_contact")) and verified,
        sender_kind="agent" if meta.get("bulk") else f.sender_kind,
        sender_name=str(meta.get("from_name") or f.sender_name),
        provider="email",
        extra={**f.extra, "verified_vip": bool(f.extra.get("vip_contact")) and verified, "source": "email",
               "email_verified": bool(meta.get("verified"))},
    )  # fmt: skip
    # A model call only for a known sender Gmail vouched for; strangers and lists are rules-only.
    allow_model = sender is not None and sender.relation != "unknown" and verified and not meta.get("bulk")
    return decide(session, event, owner, sender, f, llm=llm, model=model, ready=ready and allow_model, now=now)


# --- the poll ------------------------------------------------------------------------------


def poll(session: Session, *, llm: Any = None, model: str = "", ready: bool = False,
         now: datetime | None = None) -> int:  # fmt: skip
    """Read and triage what arrived since the last poll. Returns how many emails were triaged."""
    from superaide.app.accounts import owner as instance_owner

    owner = instance_owner(session)
    if owner is None or not gmail.can_read(session):
        return 0
    now = now or datetime.now(UTC)
    client, tok = api.http_client(), gmail.token(session)
    state = get_state(session, STATE_KEY)
    if not state.get("history_id"):
        start = str(api.profile(client, tok).get("historyId") or "")
        set_state(session, STATE_KEY, {"history_id": start, "at": now.isoformat()})
        audit(session, "skills", "gmail_sync_started")
        return 0
    try:
        ids, latest = api.history(client, tok, str(state["history_id"]))
    except api.HistoryGone:
        start = str(api.profile(client, tok).get("historyId") or "")
        set_state(session, STATE_KEY, {"history_id": start, "at": now.isoformat()})
        audit(session, "skills", "gmail_sync_restarted")
        return 0
    done = 0
    for mid in ids[:MAX_PER_POLL]:
        msg = api.message(client, tok, mid)
        if msg is None:
            continue
        event = ingest(session, owner, msg)
        if event is not None and triage(session, owner, event, llm=llm, model=model, ready=ready, now=now):
            done += 1
    set_state(session, STATE_KEY, {"history_id": latest, "at": now.isoformat()})
    audit(session, "skills", "gmail_polled", new=len(ids), triaged=done, skipped=max(0, len(ids) - MAX_PER_POLL))
    return done


@handler(JOB_KIND)
def _job(session: Session, job: Job) -> None:
    from superaide.factory import brain_ready, build_llm
    from superaide.vault.store import runtime_settings

    try:
        if enabled():
            settings = runtime_settings()
            ready = brain_ready(settings)
            poll(session, llm=build_llm(settings) if ready else None, model=settings.model_triage, ready=ready)
    except Exception as exc:  # any failure: the chain must go on (issue #86), so never let a poll raise
        log.warning("gmail poll: %s", exc, exc_info=not isinstance(exc, (google.GoogleError, gmail.NotReady)))
        audit(session, "skills", "gmail_poll_failed", error=type(exc).__name__)
    finally:
        ensure_scheduled(session, max(datetime.now(UTC), job.run_at) + timedelta(seconds=1))


# --- the digest ----------------------------------------------------------------------------


def quotable(d: TriageDecision, subject: str) -> bool:
    """Whether an email's subject (or a quote from it) may be shown: never for payment, legal or
    credential mail, judged by the rules, the classifier or the subject itself."""
    from superaide.triage.digest import QUOTE_HIDDEN

    return not (
        set((d.classifier or {}).get("risk_flags") or []) & QUOTE_HIDDEN
        or any(h.startswith(("R3_risk:", "R3_classifier:")) for h in d.rule_hits or [])
        or NEVER_QUOTED.search(subject)
    )


def digest_section(session: Session, rows: list[TriageDecision]) -> list[str]:
    """'3 emails need you; 12 handled; 40 newsletters held', then the top items, never quoting
    payment, legal or credential mail."""
    from superaide.triage.routes import bucket

    if not rows:
        return []
    events = {e.id: e for e in session.scalars(select(Event).where(Event.id.in_([d.inbound_event_id for d in rows])))}
    need, handled, newsletters, held = [], 0, 0, 0
    for d in rows:
        b = bucket(d, None)
        if b == "needs_you":
            need.append(d)
        elif b == "handled":
            handled += 1
        elif "R4_automated" in (d.rule_hits or []):
            newsletters += 1
        else:
            held += 1
    parts = [f"{len(need)} email{'s' if len(need) != 1 else ''} need{'s' if len(need) == 1 else ''} you"]
    parts.append(f"{handled} handled")
    parts.append(f"{newsletters} newsletter{'s' if newsletters != 1 else ''} held")
    if held:
        parts.append(f"{held} other{'s' if held != 1 else ''} held")
    lines = [f"**Email:** {'; '.join(parts)}"]
    for d in need[:3]:
        meta = (events[d.inbound_event_id].meta or {}) if d.inbound_event_id in events else {}
        who = str(meta.get("from_name") or "Someone")
        subject = str(meta.get("subject") or "(no subject)")
        risky = not quotable(d, subject)
        lines.append(f"- {who}: " + ("an email about money or security (not quoted)" if risky
                                      else f"“{subject[:100]}”"))  # fmt: skip
    if len(need) > 3:
        lines.append(f"- and {len(need) - 3} more in Needs you")
    return lines
