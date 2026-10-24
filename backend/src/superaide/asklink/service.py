"""The "Ask my Ava" link: finding a published handle, the public form's token, rate limits, ranking a
request in code, growth counts, and what the owner is told.

Rules this module keeps:

- **No model call for the visitor.** A request is ranked by the triage rules (triage/rules.py): the
  risk screen, the owner's urgent topics and silenced topics, and the deadline. A stranger is
  never verified, so the rules can never make a request interrupt the owner (R5 needs a verified
  VIP); the most it reaches is "urgent" (an in-app note) or "unverified".
- **No enumeration.** The public lookup is one query, `handle = :h AND published`, so an unknown, an
  unpublished and a revoked handle do the same work and get the same answer.
- **Visitor text never reaches a model.** It is stored and shown to the owner as data. Notes in the
  owner's chat and digest lines carry counts, never the visitor's words, because the owner's chat
  history is read by the model on the owner's next turn.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.asklink.models import AskHandle, AskLink, AskRate, AskRequest, AskStat
from superaide.audit import audit
from superaide.config import get_settings
from superaide.db.models import Approval, Person
from superaide.policy.registry import EXECUTORS
from superaide.tenancy.context import InstanceScope

log = logging.getLogger(__name__)

# What the visitor is told after sending, whatever happened to it (stored, or dropped as a bot).
CONFIRMATION = "Ava, an AI assistant, has passed this on. You'll hear back if it's a fit."
AI_NOTICE = (
    "You're leaving a message with Ava, an AI assistant. A person reads what you send here; "
    "Ava doesn't reply on her own."
)
GET_OWN = {"label": "Get your own Ava", "href": "/agents/ava?ref=ask"}
URGENT_WITHIN = timedelta(hours=48)
NOTE_EVERY = timedelta(minutes=15)  # at most one "new request" note in the owner's chat per this long
RANKS = ("unverified", "urgent", "normal", "low")

_process_secret = secrets.token_bytes(32)


def enabled() -> bool:
    return get_settings().ask_link_enabled


def _secret() -> bytes:
    configured = get_settings().ask_form_secret.strip()
    return hashlib.sha256(configured.encode()).digest() if configured else _process_secret


# --- the public lookup ----------------------------------------------------------------------------


def published_handle(session: Session, raw: str) -> AskHandle | None:
    """The published handle, or None for an unknown, unpublished or revoked one (the same query)."""
    h = (raw or "").strip().lower()[:41]
    return session.scalar(select(AskHandle).where(AskHandle.handle == h, AskHandle.published.is_(True)))


def scope_of(row: AskHandle) -> InstanceScope:
    return InstanceScope(tenant_id=row.tenant_id, agent_id=row.agent_id)


def live_link(session: Session, handle: str) -> AskLink | None:
    """Inside the instance in scope: its link for this handle, if it's still published."""
    return session.scalar(
        select(AskLink).where(AskLink.handle == handle, AskLink.published.is_(True), AskLink.revoked_at.is_(None))
    )


def display_line(session: Session, link: AskLink) -> tuple[str, str | None]:
    """("Jordan's assistant", "Jordan") when the owner opted in, else a line that names nobody."""
    if link.show_first_name:
        owner = session.get(Person, link.owner_person_id)
        first = (owner.display_name.split() or [""])[0][:40] if owner else ""
        if first:
            return f"{first}'s assistant", first
    return "A personal assistant's inbox", None


# --- the form token: same form, and time to submit ------------------------------------------------


def _sign(msg: str) -> str:
    mac = hmac.new(_secret(), msg.encode(), hashlib.sha256).digest()[:18]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def issue_token(handle_id: uuid.UUID, now: float | None = None) -> str:
    issued = int((now if now is not None else time.time()) * 1000)
    nonce = secrets.token_urlsafe(9)
    return f"v1.{issued}.{nonce}.{_sign(f'{handle_id}.{issued}.{nonce}')}"


def token_age(token: str, handle_id: uuid.UUID, now: float | None = None) -> float | None:
    """Seconds since the token was issued for this handle, or None if it isn't one of ours."""
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[1].isdigit():
        return None
    _, issued, nonce, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{handle_id}.{issued}.{nonce}")):
        return None
    age = (now if now is not None else time.time()) - int(issued) / 1000
    return age if age >= -5 else None


# --- rate limits ----------------------------------------------------------------------------------


def address_key(ip: str) -> str:
    """A keyed hash of the visitor's address: enough to count, never the address."""
    return hmac.new(_secret(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:24]


def hit(session: Session, bucket: str, window: timedelta, now: datetime) -> int:
    """Count one hit in this fixed window and return the window's total (counted in the database, so
    parallel requests can't race past a limit)."""
    seconds = int(window.total_seconds())
    start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, UTC)
    key = f"{bucket}:{seconds}:{int(start.timestamp())}"[:120]
    stmt = (
        insert(AskRate)
        .values(bucket=key, window_start=start, hits=1)
        .on_conflict_do_update(index_elements=[AskRate.bucket], set_={"hits": AskRate.hits + 1})
        .returning(AskRate.hits)
    )
    total = int(session.scalar(stmt) or 1)
    if secrets.randbelow(50) == 0:  # now and then, forget windows older than two days
        session.execute(delete(AskRate).where(AskRate.window_start < now - timedelta(days=2)))
    return total


def over_limit(session: Session, *, ip: str, handle_id: uuid.UUID, now: datetime) -> bool:
    s = get_settings()
    over = hit(session, f"ip:{address_key(ip)}", timedelta(hours=1), now) > s.ask_per_ip_per_hour
    over |= hit(session, f"handle:{handle_id}", timedelta(hours=1), now) > s.ask_per_handle_per_hour
    over |= hit(session, f"handle:{handle_id}", timedelta(days=1), now) > s.ask_per_handle_per_day
    return over


# --- growth counts (numbers only) -----------------------------------------------------------------

COUNTERS = ("views", "submissions", "dropped", "get_own_clicks")


def count(session: Session, field: str, scope: InstanceScope, now: datetime | None = None) -> None:
    assert field in COUNTERS
    day = (now or datetime.now(UTC)).date()
    stmt = (
        insert(AskStat)
        .values(id=uuid.uuid4(), tenant_id=scope.tenant_id, agent_id=scope.agent_id, day=day, **{field: 1})
        .on_conflict_do_update(constraint="uq_ask_stat_day", set_={field: getattr(AskStat, field) + 1})
    )
    session.execute(stmt)


def totals(session: Session, since: date | None = None) -> dict[str, int]:
    q = select(*(func.coalesce(func.sum(getattr(AskStat, f)), 0) for f in COUNTERS))
    if since is not None:
        q = q.where(AskStat.day >= since)
    row = session.execute(q).one()
    return {f: int(v) for f, v in zip(COUNTERS, row, strict=True)}


# --- cleaning and checking what the visitor typed --------------------------------------------------

# Bidirectional overrides and zero-width characters can make text read differently than it is.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
EMAIL = re.compile(
    r"^[^\s@<>()\[\],;:\"]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$"
)
PHONE = re.compile(r"^\+?[0-9 ()./-]{7,24}$")


def clean(text: str, *, multiline: bool = False) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = _INVISIBLE.sub("", text)
    kept = []
    for ch in text:
        if ch == "\n" and multiline:
            kept.append(ch)
        elif unicodedata.category(ch)[0] == "C":
            kept.append(" ")
        else:
            kept.append(ch)
    text = "".join(kept)
    if multiline:
        lines = [" ".join(line.split()) for line in text.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return " ".join(text.split())


def contact_kind(contact: str) -> str | None:
    """ "email" or "phone" for a well-formed one, else None. Never verified, never contacted."""
    c = contact.strip()
    if EMAIL.match(c.lower()):
        return "email"
    if PHONE.match(c) and 7 <= sum(ch.isdigit() for ch in c) <= 15:
        return "phone"
    return None


# --- ranking, in code ------------------------------------------------------------------------------


@dataclass
class Rank:
    rank: str
    hits: list[str]


def rank_request(
    session: Session, owner: Person, *, topic: str, why: str, deadline: date | None, now: datetime
) -> Rank:
    """The triage rules over a stranger's request. Never `interrupt`: a stranger isn't verified."""
    from superaide.intentions.when import zone
    from superaide.knowledge.store import preferences_for
    from superaide.triage import rules
    from superaide.triage.features import Features
    from superaide.triage.models import TriageSettings

    pref = preferences_for(session, owner)
    ts = session.scalar(select(TriageSettings).where(TriageSettings.person_id == owner.id))
    deadline_at = None
    if deadline is not None:
        end = datetime.combine(deadline, datetime.max.time()).replace(tzinfo=zone(pref.timezone))
        deadline_at = end.astimezone(UTC)
    f = Features(
        text=f"{topic}\n{why}",
        now=now,
        timezone=pref.timezone,
        relation="unknown",
        provider="ask_link",
        urgent_topics=tuple(t for t in (pref.urgent_topics or []) if t.strip()),
        silenced_topics=tuple(t for t in ((ts.silenced_topics if ts else None) or []) if t.strip()),
        deadline_at=deadline_at,
    )
    r = rules.evaluate(f)
    hits = ["source:ask_link", *r.hits]
    soon = deadline_at is not None and deadline_at - now <= URGENT_WITHIN
    if soon and "deadline_2h" not in hits:
        hits.append("deadline_48h")
    if "R2_silenced_topic" in hits:
        rank = "low"
    elif r.risks:
        rank = "unverified"
    elif r.topic_ids or soon:
        rank = "urgent"
    else:
        rank = "normal"
    return Rank(rank, hits)


# --- storing a request and telling the owner --------------------------------------------------------


def store_request(
    session: Session,
    link: AskLink,
    *,
    name: str,
    contact: str,
    kind: str,
    topic: str,
    why: str,
    deadline: date | None,
    now: datetime,
) -> AskRequest:
    owner = session.get(Person, link.owner_person_id)
    assert owner is not None
    r = rank_request(session, owner, topic=topic, why=why, deadline=deadline, now=now)
    req = AskRequest(
        link_id=link.id, name=name, contact=contact, contact_kind=kind, topic=topic, why=why,
        deadline=deadline, rank=r.rank, rule_hits=r.hits,
    )  # fmt: skip
    session.add(req)
    session.flush()
    audit(session, "ask_link", "request_received", request_id=req.id, rank=r.rank, rule_hits=r.hits)
    _tell_owner(session, owner, req, now)
    return req


def _tell_owner(session: Session, owner: Person, req: AskRequest, now: datetime) -> None:
    """One short in-app note, with no words from the visitor in it. With triage on, only for a request
    that may be urgent or is unverified (the rest wait for Needs you and the digest). A burst of
    requests makes one note, not many."""
    from superaide.proactive.notify import notify_owner

    triage_on = get_settings().triage_enabled
    if triage_on and req.rank not in ("urgent", "unverified"):
        return
    recent = session.scalar(
        select(func.count())
        .select_from(AskRequest)
        .where(AskRequest.id != req.id, AskRequest.created_at >= now - NOTE_EVERY)
    )
    if recent:
        return
    if req.rank == "unverified":
        text = (
            "A request from your Ask link asks for something risky (money, codes or secrecy). Check it before you act."
        )
    elif req.rank == "urgent":
        text = "A request from your Ask link may be urgent. Open your dashboard to read it."
    else:
        text = "New request from your Ask link. Open your dashboard to read it."
    notify_owner(session, owner, text, {"kind": "ask_request", "request_id": str(req.id)}, channel="app")


def digest_lines(session: Session, owner: Person, now: datetime) -> list[str]:
    """For proactive/digest.py: counts of new requests since the last digest. [] while the link is off.
    Counts only: the digest can be pushed to a chat and is read by the model in the owner's chat."""
    if not enabled():
        return []
    from superaide.triage.digest import last_digest_at

    since = last_digest_at(session, owner, now)
    rows = session.execute(
        select(AskRequest.rank, func.count())
        .where(AskRequest.created_at >= since, AskRequest.created_at < now, AskRequest.status == "new")
        .group_by(AskRequest.rank)
    ).all()
    by = {rank: int(n) for rank, n in rows}
    total = sum(by.values())
    if not total:
        return []
    extra = [f"{by[k]} {label}" for k, label in (("urgent", "maybe urgent"), ("unverified", "unverified")) if by.get(k)]
    noun = "request" if total == 1 else "requests"
    return [
        f"**New from your link:** {total} {noun}"
        + (f" ({', '.join(extra)})" if extra else "")
        + ". Read them on your dashboard."
    ]


# --- the owner's actions -------------------------------------------------------------------------


def request_view(req: AskRequest, approval: Approval | None = None) -> dict[str, Any]:
    return {
        "id": str(req.id), "name": req.name, "contact": req.contact, "contact_kind": req.contact_kind,
        "topic": req.topic, "why": req.why, "deadline": req.deadline.isoformat() if req.deadline else None,
        "rank": req.rank, "rule_hits": list(req.rule_hits or []), "status": req.status,
        "created_at": req.created_at.isoformat(),
        "reply": None if approval is None else {
            "approval_id": str(approval.id), "status": approval.status,
            "text": (approval.payload or {}).get("text", ""),
        },
    }  # fmt: skip


def scrub(req: AskRequest) -> None:
    """Spam keeps a row for the counts, and nothing the sender typed."""
    req.name, req.contact, req.topic, req.why, req.deadline = "(spam)", "", "", "", None


def _execute_reply(session: Session, approval: Approval) -> str:
    """The owner approved their reply. Superaide doesn't send to a visitor's address yet, so the
    request is marked replied and the owner sends the approved text themselves."""
    rid = (approval.payload or {}).get("request_id")
    req = session.get(AskRequest, uuid.UUID(rid)) if rid else None
    if req is None:
        return "Approved. That request is gone."
    req.status = "replied"
    req.decided_at = datetime.now(UTC)
    audit(session, "ask_link", "request_replied", request_id=req.id)
    how = "email" if req.contact_kind == "email" else "message"
    return f"Approved. Superaide can't send it for you yet: {how} it to {req.contact} yourself."


def register() -> None:
    EXECUTORS["ask_reply"] = _execute_reply


register()
