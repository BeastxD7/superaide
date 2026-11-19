"""Live human takeover (#493): the owner steps into a visitor's conversation and Ava steps out.

While a takeover is active for a conversation, code decides that Ava does not reply there (`pipeline.Agent._handle`
asks `active`). Nothing about it is the model's:

- Who can start one: the signed-in owner, from the app, for a conversation that has a visitor in it
  (`is_visitor_context`). The owner's words are sent as they typed them, behind a fixed label saying who is writing and
  that it is not Ava, and stored in the conversation marked `owner_live`.
- What Ava still does: stores what the visitor writes (the owner reads it in the app) and reads what the owner wrote
  when she resumes (`build_messages` shows it as the owner's own words). Audience rules are unchanged: those messages
  carry the conversation's audience, so they reach no other conversation.
- When she resumes: when the owner presses "Give control back to Ava" or tells her to (`parse_release`), or after the
  owner has written nothing for the idle window (150 minutes unless the owner changed it). The window is checked
  whenever the conversation is read (`active`) and by the `takeover_expire` job, so a stopped worker cannot keep her
  silent.

The visitor is not told that Ava stepped out or back in: the owner's own messages show it. Not here yet: starting a
takeover from a message the owner types inside the platform's own app (Telegram/Slack), which a bot cannot see for a
private chat; and a visitor-facing "a person is here" line."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.jobs import queue as jobs
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.reception.models import ReceptionMessage, ReceptionSettings, Takeover

log = logging.getLogger(__name__)

MIN_MINUTES = 5
MAX_MINUTES = 1440
MAX_TEXT = 1000
PING_EVERY = timedelta(minutes=10)  # at most one "they wrote again" note per takeover in this time
SHOWN = 12  # messages of the conversation shown to the owner beside the panel
PING = "A person you're chatting with wrote again. Open Settings to read it and reply: /dashboard/settings#takeover"
BACK = "{agent} is back in your chat with {who} after {minutes} minutes without a message from you."
RELEASE_CMD = re.compile(
    r"^\s*(?:(?:ava|hey ava)[,:]?\s+)?(?:please\s+)?(?:take (?:it |the (?:chat|conversation) )?(?:back|over again)|"
    r"(?:give|hand)(?: (?:control|the (?:chat|conversation)|it))? back(?: to ava)?|"
    r"resume (?:the )?(?:chat|conversation)?)"
    r"(?:\s+(?:with|for|on|to|from|in)\s+(?P<who>[\w' .-]{1,60}?))?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def enabled() -> bool:
    return get_settings().takeover_idle_minutes > 0


def window_minutes_default() -> int:
    return max(MIN_MINUTES, min(MAX_MINUTES, get_settings().takeover_idle_minutes))


def window_minutes(session: Session, owner_id: uuid.UUID) -> int:
    """Minutes of owner silence before Ava resumes: the owner's own setting, else the deployment default."""
    row = session.scalar(select(ReceptionSettings.takeover_idle_minutes).where(ReceptionSettings.person_id == owner_id))
    base = get_settings().takeover_idle_minutes
    return max(MIN_MINUTES, min(MAX_MINUTES, row if row else base)) if base > 0 else 0


def resumes_at(session: Session, row: Takeover) -> datetime:
    return row.last_owner_message_at + timedelta(minutes=window_minutes(session, row.owner_person_id))


def _row(session: Session, context_id: uuid.UUID) -> Takeover | None:
    return session.scalar(select(Takeover).where(Takeover.context_id == context_id, Takeover.status == "active"))


def release(session: Session, row: Takeover, reason: str, now: datetime | None = None) -> None:
    row.status, row.ended_at, row.ended_reason = "released", now or datetime.now(UTC), reason
    audit(session, "takeover", "released", context_id=row.context_id, reason=reason)
    if reason == "idle":
        _tell_owner_back(session, row)


def _tell_owner_back(session: Session, row: Takeover) -> None:
    """Fixed text, no visitor words: Ava took the conversation back by herself, so the owner is told (in the app)."""
    from superaide.proactive.notify import notify_owner

    owner = session.get(Person, row.owner_person_id)
    ctx = session.get(Context, row.context_id)
    if owner is None or ctx is None:
        return
    who = visitor_name(session, ctx) or "that person"
    minutes = window_minutes(session, row.owner_person_id)
    text = BACK.format(agent=get_settings().agent_name, who=who, minutes=minutes)
    try:
        with session.begin_nested():
            notify_owner(session, owner, text, {"kind": "takeover_back", "source": f"takeover:{row.id}"}, channel="app")
    except Exception:
        log.exception("couldn't tell the owner the chat is back with Ava")


def visitor_wrote(session: Session, row: Takeover, now: datetime | None = None) -> None:
    """The visitor wrote while the owner is handling it: the owner is told once in a while, in fixed words, and reads
    the message in the app (it is data there, quoted; it is never put in the notification or in the owner's chat)."""
    from superaide.proactive.notify import notify_owner

    now = now or datetime.now(UTC)
    if row.notified_at is not None and now - row.notified_at < PING_EVERY:
        return
    owner = session.get(Person, row.owner_person_id)
    if owner is None:
        return
    row.notified_at = now
    try:
        with session.begin_nested():
            notify_owner(session, owner, PING, {"kind": "takeover_visitor", "source": f"takeover:{row.id}"})
    except Exception:
        log.exception("couldn't tell the owner the visitor wrote")


def recent(session: Session, context_id: uuid.UUID) -> list[dict[str, str]]:
    """The last messages of the conversation for the owner's panel. `text` is the visitor's own words where `from` is
    "visitor": unverified data, shown quoted."""
    rows = session.scalars(
        select(Event)
        .where(Event.context_id == context_id, ~Event.meta.has_key("ambient"))
        .order_by(Event.created_at.desc(), Event.id)
        .limit(SHOWN)
    ).all()
    out = []
    for e in reversed(rows):
        who = "visitor" if e.direction == "in" else ("owner" if (e.meta or {}).get("owner_live") else "ava")
        out.append({"from": who, "text": e.content[:500], "at": e.created_at.isoformat()})
    return out


def active(session: Session, context_id: uuid.UUID, now: datetime | None = None) -> Takeover | None:
    """The takeover in force for this conversation, or None. One past its idle window is released here and never
    applies, whether or not the job has run yet."""
    if not enabled():
        return None
    now = now or datetime.now(UTC)
    row = _row(session, context_id)
    if row is None:
        return None
    if resumes_at(session, row) <= now:
        release(session, row, "idle", now)
        return None
    return row


def start_or_refresh(session: Session, ctx: Context, owner: Person, now: datetime | None = None) -> Takeover:
    """The owner wrote in this conversation: Ava is out of it, and the idle window starts again."""
    now = now or datetime.now(UTC)
    row = _row(session, ctx.id)
    if row is None:
        row = Takeover(context_id=ctx.id, owner_person_id=owner.id, started_at=now, last_owner_message_at=now)
        session.add(row)
        session.flush()
        audit(session, "takeover", "started", context_id=ctx.id, provider=ctx.provider)
    else:
        row.last_owner_message_at = now
    _schedule(session, row)
    return row


def _schedule(session: Session, row: Takeover) -> None:
    """A job at the end of the window. A newer owner message makes this one a no-op (it re-reads the row)."""
    at = resumes_at(session, row)
    jobs.enqueue(
        session,
        "takeover_expire",
        {"takeover_id": str(row.id)},
        run_at=at + timedelta(seconds=1),
        dedupe_key=f"takeover:{row.id}:{at.isoformat()}",
    )


@jobs.handler("takeover_expire")
def _expire_job(session: Session, job: Any) -> None:
    row = session.get(Takeover, uuid.UUID(str(job.payload["takeover_id"])))
    if row is None or row.status != "active":
        return
    now = datetime.now(UTC)
    if resumes_at(session, row) <= now:  # else the owner wrote since this job was queued, and that write queued its own
        release(session, row, "idle", now)


def is_visitor_context(session: Session, ctx: Context, owner_id: uuid.UUID) -> bool:
    """A one-to-one conversation on Telegram or Slack in which someone other than the owner has written. The owner's own
    conversations (only they write there) and rooms are never taken over."""
    if ctx.kind != "dm" or ctx.provider not in ("telegram", "slack"):
        return False
    other = session.scalar(
        select(Event.id)
        .where(
            Event.context_id == ctx.id,
            Event.direction == "in",
            (Event.resolved_person_id.is_(None)) | (Event.resolved_person_id != owner_id),
        )
        .limit(1)
    )
    return other is not None


def visitor_name(session: Session, ctx: Context) -> str:
    """Their first word of what they called themselves (unverified: display only), else the platform's title."""
    given = session.scalar(
        select(ReceptionMessage.name)
        .where(ReceptionMessage.context_id == ctx.id, ReceptionMessage.name != "")
        .order_by(ReceptionMessage.created_at.desc())
        .limit(1)
    )
    word = ((given or ctx.title or "").split() or [""])[0]
    return "".join(c for c in word if c.isalpha() or c in "'-")[:20].strip("'-")


def parse_release(text: str) -> tuple[bool, str]:
    """(is it the command, the name after it): "Give control back to Ava", "take it back", "hand back for Meera"."""
    m = RELEASE_CMD.match(text or "")
    if m is None:
        return False, ""
    who = (m.group("who") or "").strip()
    return True, "" if who.lower() in ("ava", "you") else who


def release_by_command(session: Session, owner: Person, text: str, here: Context | None = None) -> str | None:
    """The owner's chat command. In the taken-over conversation itself it releases that one; in their own chat it
    releases the one named, or the only one there is. None: not the command. Otherwise the line to answer with."""
    is_cmd, who = parse_release(text)
    if not is_cmd or not enabled():
        return None
    if here is not None:
        row = _row(session, here.id)
        if row is None:
            return None
        release(session, row, "command")
        return "I'm back in this conversation."
    rows = list(session.scalars(select(Takeover).where(Takeover.status == "active").order_by(Takeover.started_at)))
    named = [(r, visitor_name(session, c)) for r in rows if (c := session.get(Context, r.context_id)) is not None]
    if who:
        named = [(r, n) for r, n in named if n and n.lower() == who.split()[0].lower()]
    if not named:
        return "You aren't handling any conversation right now, so there's nothing to give back."
    if len(named) > 1:
        names = ", ".join(n or "someone" for _, n in named)
        example = named[0][1] or "them"
        return (
            f"You're handling {len(named)} conversations ({names}). Say which, like “give control back for {example}”."
        )
    release(session, named[0][0], "command")
    who_text = named[0][1] or "that person"
    return f"Done. I'm back in the conversation with {who_text}."


def view(session: Session, row: Takeover) -> dict[str, Any]:
    return {
        "started_at": row.started_at.isoformat(),
        "last_owner_message_at": row.last_owner_message_at.isoformat(),
        "resumes_at": resumes_at(session, row).isoformat(),
    }
