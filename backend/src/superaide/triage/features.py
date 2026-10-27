"""What triage knows about one inbound message, gathered in code from typed data only
(ADR 0010 section 2): who sent it and how verified they are, the owner's contacts, preferences
and triage settings, quiet hours, away and busy, the interrupt budget, and the stage.

The owner's free-text `never_share` is never a triage input. VIP status attaches only to a
verified (linked) channel identity.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.identity.trust import Trust
from superaide.intentions.when import find_moment, zone
from superaide.memory.knowledge.profile import in_quiet_hours
from superaide.memory.knowledge.store import preferences_for
from superaide.platform.db.models import ChannelIdentity, Contact, Context, Event, Person
from superaide.triage.models import EscalationDelivery
from superaide.triage.store import current_stage, settings_for
from superaide.triage.types import Stage

# A message that says it's urgent. On its own this is never evidence (anyone can type "urgent!!");
# it only counts for a verified sender who repeats it (R6).
URGENT_WORDS = re.compile(
    r"\b(?:urgent(?:ly)?|asap|emergency|immediately|right away|right now|critical|time[- ]sensitive)\b|!{2,}", re.I
)
REPEAT_WINDOW = timedelta(minutes=3)
AUTOMATED_NAME = re.compile(r"\b(?:no[-_ ]?reply|do[-_ ]?not[-_ ]?reply|notifications?|mailer[-_ ]daemon|bot)\b", re.I)


@dataclass(frozen=True)
class Features:
    """Everything the rules and the policy read. Pure data, so they are testable without a database."""

    text: str
    now: datetime
    timezone: str = "UTC"
    sender_person_id: uuid.UUID | None = None
    relation: str = "unknown"  # manager | colleague | contact | external | unknown
    sender_kind: str = "human"  # human | agent
    sender_name: str = ""
    vip: bool = False  # a VIP contact *and* the identity is verified
    trust: int = 0
    chat_kind: str = "dm"  # dm | group
    provider: str = "web"
    audience_class: str = "private_1to1"
    quiet: bool = False
    quiet_end_at: datetime | None = None
    vip_breaks_quiet: bool = True
    away_until: datetime | None = None
    busy_until: datetime | None = None
    repeats: int = 0  # urgent-sounding messages from this verified sender in the last 3 minutes, this one included
    interrupts_last_hour: int = 0
    interrupts_today: int = 0
    max_per_hour: int = 2
    max_per_day: int = 6
    vip_ignores_budget: str = "critical"
    theta_interrupt: float = 0.6
    theta_handle: float = 0.7
    stage: Stage = Stage.INFANT
    urgent_topics: tuple[str, ...] = ()
    silenced_people: tuple[uuid.UUID, ...] = ()
    silenced_topics: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()
    deadline_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.trust >= int(Trust.LINKED)

    @property
    def known(self) -> bool:
        return self.sender_person_id is not None and self.relation != "unknown"

    @property
    def away(self) -> bool:
        return self.away_until is not None and self.away_until > self.now

    @property
    def busy(self) -> bool:
        return self.busy_until is not None and self.busy_until > self.now

    @property
    def budget_left(self) -> int:
        return max(0, min(self.max_per_hour - self.interrupts_last_hour, self.max_per_day - self.interrupts_today))

    @property
    def automated(self) -> bool:
        return self.sender_kind == "agent" or bool(AUTOMATED_NAME.search(self.sender_name))

    def snapshot(self) -> dict[str, Any]:
        """What's stored on the decision row: labels and numbers, never the message text."""
        out = asdict(self)
        for drop in ("text", "extra", "urgent_topics", "silenced_people", "silenced_topics", "grants", "now"):
            out.pop(drop, None)
        out.update(
            stage=str(self.stage), verified=self.verified, away=self.away, busy=self.busy,
            budget_left=self.budget_left, sender_person_id=str(self.sender_person_id or ""),
        )  # fmt: skip
        for k, v in list(out.items()):
            if isinstance(v, datetime):
                out[k] = v.isoformat()
        return {**out, **self.extra}


def next_local(now: datetime, tz: str, hhmm: str) -> datetime:
    """The next moment (after now) that the owner's clock shows hh:mm. DST-safe: built in local time."""
    local = now.astimezone(zone(tz))
    at = datetime.combine(local.date(), time.fromisoformat(hhmm), tzinfo=local.tzinfo)
    if at <= local:
        at = datetime.combine(local.date() + timedelta(days=1), time.fromisoformat(hhmm), tzinfo=local.tzinfo)
    return at.astimezone(UTC)


def interrupts_since(session: Session, owner: Person, since: datetime) -> int:
    """Escalations that reached a chat channel (the first push, step ≥ 1) since then."""
    from superaide.triage.models import Escalation

    return int(
        session.scalar(
            select(func.count(func.distinct(EscalationDelivery.escalation_id)))
            .join(Escalation, Escalation.id == EscalationDelivery.escalation_id)
            .where(
                Escalation.owner_person_id == owner.id,
                EscalationDelivery.step >= 1,
                EscalationDelivery.status == "sent",
                EscalationDelivery.sent_at >= since,
            )
        )
        or 0
    )


def gather(session: Session, *, event: Event, owner: Person, now: datetime | None = None) -> Features:
    now = now or datetime.now(UTC)
    pref = preferences_for(session, owner)
    ts = settings_for(session, owner)
    ctx = session.get(Context, event.context_id)
    sender = session.get(Person, event.resolved_person_id) if event.resolved_person_id else None
    identity = session.get(ChannelIdentity, event.sender_identity_id) if event.sender_identity_id else None
    trust = int(identity.trust_level) if identity is not None else int(event.trust_at_time or 0)
    if identity is not None and identity.lifecycle != "active":
        trust = 0

    vip = False
    if sender is not None:
        vip = bool(
            session.scalar(
                select(Contact.vip).where(
                    Contact.owner_person_id == owner.id, Contact.person_id == sender.id, Contact.removed_at.is_(None)
                )
            )
        )
    verified = trust >= int(Trust.LINKED)

    repeats = 0
    if sender is not None and verified and URGENT_WORDS.search(event.content or ""):
        recent = session.scalars(
            select(Event.content).where(
                Event.direction == "in",
                Event.resolved_person_id == sender.id,
                Event.created_at >= now - REPEAT_WINDOW,
                Event.id != event.id,
            )
        ).all()
        repeats = 1 + sum(1 for c in recent if URGENT_WORDS.search(c or ""))

    deadline = None
    found = find_moment(event.content or "", now.astimezone(zone(pref.timezone)))
    if found is not None and found[0] > now:
        deadline = found[0].astimezone(UTC)

    quiet = in_quiet_hours(pref, now)
    return Features(
        text=event.content or "",
        now=now,
        timezone=pref.timezone,
        sender_person_id=sender.id if sender else None,
        relation=(sender.relation if sender else "unknown") or "unknown",
        sender_kind=(sender.kind if sender else "human") or "human",
        sender_name=(sender.display_name if sender else "") or "",
        vip=vip and verified,
        trust=trust,
        chat_kind=ctx.kind if ctx else "dm",
        provider=ctx.provider if ctx else "web",
        audience_class=ctx.audience_class if ctx else "private_1to1",
        quiet=quiet,
        quiet_end_at=next_local(now, pref.timezone, pref.quiet_end) if quiet else None,
        vip_breaks_quiet=pref.vip_breaks_quiet,
        away_until=ts.away_until,
        busy_until=ts.busy_until,
        repeats=repeats,
        interrupts_last_hour=interrupts_since(session, owner, now - timedelta(hours=1)),
        interrupts_today=interrupts_since(session, owner, now - timedelta(hours=24)),
        max_per_hour=ts.max_per_hour,
        max_per_day=ts.max_per_day,
        vip_ignores_budget=ts.vip_ignores_budget,
        theta_interrupt=ts.theta_interrupt,
        theta_handle=ts.theta_handle,
        stage=current_stage(session),
        urgent_topics=tuple(t for t in (pref.urgent_topics or []) if t.strip()),
        silenced_people=tuple(ts.silenced_people or []),
        silenced_topics=tuple(t for t in (ts.silenced_topics or []) if t.strip()),
        grants=tuple(ts.autonomy_grants or []),
        deadline_at=deadline,
        extra={"vip_contact": vip, "verified_vip": vip and verified},
    )
