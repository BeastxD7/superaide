"""Is this lesson safe to share? Decided in code, never by a model (#57).

A lesson must be generic advice, so it is refused when it could point back at its owner:
- an email address, a link, an @handle, or a phone-like or long number;
- any name or word the owner has given Ava about a person: display names, the owner's own name, the
  email names of their accounts and contacts (whole words, any case);
- a run of four words that also appears in anything private Ava holds: a note (`memory_item`) or a
  message (`event`). This is the canary check: a lesson cannot quote a private memory.

These are floors, not a guarantee. A human still reads the file before it goes anywhere (the export is
a file the owner opens first), and a rare phrase can slip past a regex, which is why nothing is sent
over the network from here.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.db.models import Event, MemoryItem, Person, UserAccount
from superaide.tenancy.context import scope_or_default

MAX_CHARS = 300
MIN_CHARS = 12
SHINGLE = 4
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LINK = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|org|net|io|ai|dev|app|co|in|sg)\b", re.I)
HANDLE = re.compile(r"(?:^|\s)@\w{2,}")
NUMBER = re.compile(r"\d{3,}|\d[\d\s().-]{6,}\d")
WORD = re.compile(r"[a-z0-9']+")
SKIP_NAME_WORDS = {"the", "and", "for", "mr", "mrs", "ms", "dr", "inc", "ltd", "llc", "team", "other", "owner"}


@dataclass(frozen=True)
class Problem:
    code: str  # too_short | too_long | email | link | handle | number | name | private_text
    message: str


def _words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def known_names(session: Session, owner_person_id: uuid.UUID) -> set[str]:
    """Words that identify someone in this instance: every person Ava knows here, and the owner's own
    account name and email name. Accounts of other owners are never read."""
    agent = scope_or_default().agent_id
    names: set[str] = set()
    for (display,) in session.execute(select(Person.display_name).where(Person.agent_id == agent)):
        names.update(_words(display or ""))
    mine = session.execute(
        select(UserAccount.email, UserAccount.display_name).where(UserAccount.person_id == owner_person_id)
    ).first()
    if mine is not None:
        names.update(_words(mine.display_name or ""))
        names.update(_words((mine.email or "").split("@")[0]))
    return {n for n in names if len(n) >= 3 and n not in SKIP_NAME_WORDS}


def _private_shingles(session: Session) -> set[tuple[str, ...]]:
    agent = scope_or_default().agent_id
    shingles: set[tuple[str, ...]] = set()
    texts = list(session.scalars(select(MemoryItem.content).where(MemoryItem.agent_id == agent)))
    texts += list(
        session.scalars(select(Event.content).where(Event.agent_id == agent, Event.content != "").limit(5000))
    )
    for text in texts:
        w = _words(text)
        shingles.update(tuple(w[i : i + SHINGLE]) for i in range(len(w) - SHINGLE + 1))
    return shingles


def check(
    session: Session,
    owner_person_id: uuid.UUID,
    text: str,
    *,
    names: set[str] | None = None,
    private: set[tuple[str, ...]] | None = None,
) -> list[Problem]:
    """Everything wrong with sharing this text; empty means it passes the code checks. `names` and `private`
    can be passed in when checking many lessons, so the owner's data is read once."""
    out: list[Problem] = []
    body = text.strip()
    if len(body) < MIN_CHARS:
        out.append(Problem("too_short", "Say a little more: a lesson is a full sentence of advice."))
    if len(body) > MAX_CHARS:
        out.append(Problem("too_long", f"Keep it under {MAX_CHARS} characters."))
    if EMAIL.search(body):
        out.append(Problem("email", "Remove the email address."))
    if LINK.search(body):
        out.append(Problem("link", "Remove the link."))
    if HANDLE.search(body):
        out.append(Problem("handle", "Remove the @handle."))
    if NUMBER.search(body):
        out.append(Problem("number", "Remove numbers like phone numbers, amounts or ids."))
    words = _words(body)
    hit = sorted(set(words) & (names if names is not None else known_names(session, owner_person_id)))
    if hit:
        out.append(Problem("name", f'This mentions a name Ava knows ({", ".join(hit)}). Say "a colleague" instead.'))
    shingles = private if private is not None else _private_shingles(session)
    if any(tuple(words[i : i + SHINGLE]) in shingles for i in range(len(words) - SHINGLE + 1)):
        out.append(Problem("private_text", "Part of this repeats something from your private notes or messages."))
    return out
