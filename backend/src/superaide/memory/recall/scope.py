"""What a recall question is about, decided in code: a time range, people, and kinds of record (#644).

`understand` reads the model's tool arguments and the owner's own message. "what did Meera say last week about the
invoice?" becomes: messages from the sender(s) called Meera, written between Monday and Sunday of last week, matching
"invoice". The model can pass `since`, `until`, `person` and `kinds` itself, but never decides what a word means.

Privacy: people are resolved only for the owner's own private audience (the only one that reads visitor messages), so a
colleague or a stranger cannot learn who has written to Ava by asking for a name. Time and people only narrow what the
audience gate already allows; they never widen it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.memory.audience import Audience
from superaide.memory.models import Contact
from superaide.memory.recall import lexicon, timewords
from superaide.platform.tenancy.service import instance_owner

KINDS = ("notes", "messages", "visitors", "files")
CHANNEL_LABEL = {"telegram": "Telegram", "slack": "Slack"}


@dataclass
class PersonMatch:
    label: str  # "Meera Iyer (Telegram)"
    identity_ids: set[uuid.UUID] = field(default_factory=set)  # a visitor's channel identity
    person_ids: set[uuid.UUID] = field(default_factory=set)  # a known person


@dataclass
class Scope:
    text: str  # the question for notes and files (relative time words removed)
    message_text: str  # the question for messages (time and name words removed)
    tz: str = "UTC"
    since: datetime | None = None  # a range read from the words: messages only
    until: datetime | None = None
    span: timewords.Span | None = None
    hard_since: datetime | None = None  # a range the model passed: every kind
    hard_until: datetime | None = None
    asked_person: str = ""  # the name(s) asked for, as written
    people: list[PersonMatch] = field(default_factory=list)
    kinds: set[str] = field(default_factory=lambda: set(KINDS))
    detailed: bool = False  # the owner's private audience: the empty-result note may say what was searched

    @property
    def filtered(self) -> bool:
        """True when a person or a time range narrows the messages."""
        return bool(self.asked_person) or self.since is not None or self.until is not None

    @property
    def identity_ids(self) -> set[uuid.UUID] | None:
        """The visitor identities asked for: None when no person was asked for, an empty set when nobody matched."""
        if not self.asked_person:
            return None
        return {i for p in self.people for i in p.identity_ids}

    @property
    def person_ids(self) -> set[uuid.UUID] | None:
        if not self.asked_person:
            return None
        return {i for p in self.people for i in p.person_ids}

    def range_note(self) -> str:
        if self.span is not None:
            return self.span.describe(self.tz)
        return timewords.Span(self.hard_since, self.hard_until, "", False).describe(self.tz)

    def nothing(self, channel: str | None) -> str:
        """The tool's answer when nothing was found: what was searched, so the model can say so honestly."""
        if not self.detailed:
            return "Nothing in your notes or messages matches that."
        who = ""
        if self.asked_person and not self.people:
            return (
                f"Nobody called {self.asked_person!r} has written to Ava or is in your contacts, so there is nothing "
                "to find from them. Say so, and do not guess."
            )
        if self.people:
            who = " from " + " or ".join(dict.fromkeys(p.label for p in self.people))
        when = f" {self.range_note()}".rstrip()
        where = f" on {CHANNEL_LABEL.get(channel or '', channel)}" if channel else ""
        topic = " ".join(lexicon.content_words(self.message_text))
        about = f" about {topic!r}" if topic else ""
        return f"Nothing{who}{when}{where}{about} in your notes or messages. Say so plainly; do not guess."


def _is_owner(session: Session, aud: Audience) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(the owner's person id, this instance's agent id) for the owner's own private audience, else None."""
    found = instance_owner(session)
    if found is None or aud.has_unknown or aud.person_ids != {found[1].id}:
        return None
    return found[1].id, found[1].agent_id


def _zone(session: Session) -> str:
    from superaide.memory.knowledge.store import preferences_for

    found = instance_owner(session)
    return (preferences_for(session, found[1]).timezone or "UTC") if found else "UTC"


def _candidates(session: Session, owner_id: uuid.UUID, agent_id: uuid.UUID) -> list[tuple[str, PersonMatch]]:
    """(a name, who it belongs to): names visitors gave, and the owner's contacts."""
    from superaide.identity.models import Person
    from superaide.reception.models import ReceptionMessage

    out: list[tuple[str, PersonMatch]] = []
    seen: set[uuid.UUID] = set()
    rows = session.execute(
        select(ReceptionMessage.identity_id, ReceptionMessage.name, ReceptionMessage.provider)
        .where(ReceptionMessage.agent_id == agent_id, ReceptionMessage.name != "")
        .order_by(ReceptionMessage.created_at.desc())
    )
    for identity_id, name, provider in rows:
        if identity_id in seen:
            continue
        seen.add(identity_id)
        label = f"{name} ({CHANNEL_LABEL.get(provider, provider)})"
        out.append((name, PersonMatch(label, identity_ids={identity_id})))
    for pid, name in session.execute(
        select(Person.id, Person.display_name)
        .join(Contact, Contact.person_id == Person.id)
        .where(Contact.owner_person_id == owner_id, Contact.removed_at.is_(None), Person.display_name != "")
    ):
        out.append((name, PersonMatch(name, person_ids={pid})))
    return out


def _token_match(word: str, token: str) -> bool:
    if word == token:
        return True
    return len(word) >= 5 and len(token) >= 5 and SequenceMatcher(None, word, token).ratio() >= 0.84


def resolve_people(
    session: Session, owner_id: uuid.UUID, agent_id: uuid.UUID, words: list[str]
) -> tuple[list[PersonMatch], list[str]]:
    """The people whose name has one of `words` as a name part (a typo of five letters or more is tolerated), and the
    words that matched (so the caller leaves them out of the text search)."""
    matches: list[PersonMatch] = []
    used: list[str] = []
    for name, person in _candidates(session, owner_id, agent_id):
        tokens = [t for t in lexicon.words(name) if len(t) >= 3]
        hit = [w for w in words if any(_token_match(w, t) for t in tokens)]
        if hit:
            matches.append(person)
            used += [w for w in hit if w not in used]
    return matches, used


def _strip(text: str, drop: list[str]) -> str:
    if not drop:
        return text
    gone = set(drop)
    return " ".join(w for w in text.split() if not (set(lexicon.words(w)) & gone))


def understand(
    session: Session,
    aud: Audience,
    query: str,
    *,
    asked: str = "",
    since: str = "",
    until: str = "",
    person: str = "",
    kinds: list[str] | None = None,
    now: datetime | None = None,
) -> Scope:
    """The scope of one recall call. `query` is the search text (the model's), `asked` the owner's own message, which
    may name a time or a person the model left out."""
    now = now or datetime.now(UTC)
    tz = _zone(session)
    owner = _is_owner(session, aud)
    scope = Scope(text=query, message_text=query, tz=tz, detailed=owner is not None)
    wanted = {k for k in (kinds or []) if k in KINDS}
    scope.kinds = wanted or set(KINDS)

    span, rest = timewords.find_span(query, now, tz)
    if span is None and asked:
        span, _ = timewords.find_span(asked, now, tz)
    if span is not None:
        scope.span, scope.since, scope.until = span, span.since, span.until
        scope.message_text = rest
        if span.relative:  # "last week" is not a topic for notes either; "in October" can be one
            scope.text = rest
    scope.hard_since = timewords.parse_date(since, tz)
    scope.hard_until = timewords.parse_date(until, tz, end=True)
    if scope.hard_since is not None:
        scope.since = scope.hard_since
    if scope.hard_until is not None:
        scope.until = scope.hard_until

    if owner is not None:
        given = lexicon.words(person) if person.strip() else []
        pool = given or [w for w in lexicon.content_words(query + " " + asked)]
        found, used = resolve_people(session, owner[0], owner[1], pool)
        if found:
            scope.people = found
            scope.asked_person = person.strip() or " ".join(used)
            scope.message_text = _strip(scope.message_text, used)
        elif given:  # the model asked for someone nobody matches: say so rather than ignore it
            scope.asked_person = person.strip()
    return scope


def range_args(scope: Scope, *, hard_only: bool) -> dict[str, Any]:
    """`since` and `until` for a query: the words' range for messages, the model's own for every kind."""
    if hard_only:
        return {"since": scope.hard_since, "until": scope.hard_until}
    return {"since": scope.since, "until": scope.until}
