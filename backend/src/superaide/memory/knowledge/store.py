"""Writing what the assistant learns about its owner, with provenance.

Structured settings (people, urgency, quiet hours, sharing) live in typed tables
(Contact, OwnerPreference). Free-form knowledge lives in MemoryItem rows, each
tagged with a category, a source and, for files and links, the Document it came
from. Owner facts are private to the owner unless marked shareable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, and_, func, literal, select, update
from sqlalchemy.orm import Session

from superaide.identity.auth.models import UserAccount
from superaide.identity.models import Person
from superaide.memory.consolidation import importance as weigh
from superaide.memory.knowledge import trust
from superaide.memory.models import Contact, Document, MemoryItem, OwnerPreference
from superaide.platform.audit import audit
from superaide.platform.db.session import rows_affected

CATEGORIES = ("about", "work", "person", "preference", "urgency", "availability", "sharing", "other")
SOURCES = (
    "chat",
    "intro_chat",
    "intro_call",
    "document",
    "link",
    "telegram",
    "slack",
    "owner_edit",
    "gmail",
    "calendar",
)


def channel_source(provider: str | None) -> str:
    """The `source` of a fact the owner said on this channel: "telegram" and "slack" keep their
    name so the notebook can say where it was learned; the app's chat is just "chat"."""
    return provider if provider in ("telegram", "slack") else "chat"


DUPLICATE_SIMILARITY = 0.8  # pg_trgm similarity at which two of the owner's facts are one


def find_same_fact(
    session: Session, owner: Person, content: str, *, similarity: float | None = DUPLICATE_SIMILARITY
) -> MemoryItem | None:
    """A live fact about the owner that says the same thing: equal once normalised (case,
    punctuation, en-GB/en-US spelling) or, with `similarity`, at least that close by trigrams.
    Any audience: a caller that merges into it must check the audiences match."""
    from superaide.memory.recall.lexicon import normalise
    from superaide.memory.recall.retrieval import _has_trgm

    norm = normalise(content)
    live = and_(
        MemoryItem.valid_to.is_(None),
        MemoryItem.conflict_status == "none",  # a claim waiting for the owner is not yet a fact
        MemoryItem.agent_id == owner.agent_id,
        MemoryItem.subject_person_ids.contains([owner.id]),
    )
    if _has_trgm(session):
        score = func.similarity(MemoryItem.content, content)
        rows = session.execute(
            select(MemoryItem, score).where(live, score >= 0.5).order_by(score.desc()).limit(20)
        ).all()  # normalised-equal texts score well above 0.5
    else:
        rows = session.execute(
            select(MemoryItem, literal(0.0, Float)).where(live).order_by(MemoryItem.created_at.desc()).limit(500)
        ).all()
    for row, sc in rows:
        if normalise(row.content) == norm or (similarity is not None and sc >= similarity):
            return row
    return None


def remember_fact(
    session: Session,
    *,
    owner: Person,
    content: str,
    category: str = "other",
    source: str = "chat",
    document: Document | None = None,
    confidence: float = 1.0,
    shareable: bool = False,
    origin_context_id: Any = None,
    origin_url: str | None = None,
    origin_author: str | None = None,
    quote_chain: tuple[str, ...] = (),
    importance: float | None = None,
    time_bound: bool | None = None,
    trust_rank: int | None = None,
    author_person_id: Any = None,
    source_ref: str | None = None,
) -> MemoryItem | None:
    """Store one fact about the owner. Returns None if the same fact is already known (with the Echo Filter
    on, a repeat from a *different* root origin is still noted on the known fact). Its trust rank comes from its
    source unless given; a link fact outside about/work is held for review."""
    from superaide.memory.consolidation import echo
    from superaide.platform.config import get_settings

    hearsay = get_settings().echo_filter_enabled and source in echo.HEARSAY_SOURCES
    content = " ".join(content.split())[:2000]
    if not content:
        return None
    category = category if category in CATEGORIES else "other"
    root = None
    if hearsay:
        url = origin_url or (document.source if document is not None and document.kind in ("web", "github") else None)
        root = echo.origin_root(url=url, author=origin_author, quote_chain=quote_chain)
    known = find_same_fact(session, owner, content, similarity=None)
    if known is not None:
        if hearsay:
            echo.record_origin(session, known, root)
        return None
    item = MemoryItem(
        belief_state="hearsay" if hearsay else "fact",
        content=content,
        subject_person_ids=[owner.id],
        origin_context_id=origin_context_id,
        allowed_kind="public" if shareable else "persons",
        allowed_person_ids=[] if shareable else [owner.id],
        min_trust=0 if shareable else 2,
        category=category,
        source=source if source in SOURCES else "chat",
        document_id=document.id if document else None,
        confidence=max(0.0, min(1.0, confidence)),
        importance=weigh.score(category, importance),
        time_bound=weigh.is_time_bound(category, content) if time_bound is None else time_bound,
        trust_rank=trust_rank if trust_rank is not None else trust.rank_for(source),
        author_person_id=author_person_id if author_person_id is not None else owner.id,
        conflict_status="review" if trust.needs_review(source, category) else "none",
        source_ref=source_ref,
    )
    session.add(item)
    session.flush()
    if hearsay:
        echo.record_origin(session, item, root)
    from superaide.memory.recall import vectors

    vectors.schedule(session, item)
    audit(session, "memory", "remembered", memory_id=item.id, source=item.source, category=item.category)
    return item


def supersede(session: Session, old: MemoryItem, new: MemoryItem) -> bool:
    """`new` says `old` is no longer true. True when `old` retires (new is as trusted or better, and it
    stays queryable by valid time, with `superseded_by` pointing at its successor); False when `new` is
    weaker or `old` is locked by the owner, so `old` stays and `new` waits as a conflict for the owner to resolve."""
    if old.valid_to is not None:
        return True
    if new.trust_rank <= old.trust_rank and not old.locked:  # a locked fact only the owner may replace
        old.valid_to = datetime.now(UTC)
        old.superseded_by = new.id
        audit(session, "memory", "superseded", memory_id=old.id, by=new.id)
        return True
    new.conflict_status = "pending"
    new.conflicts_with_id = old.id
    audit(
        session, "memory", "conflict_raised", memory_id=new.id, against=old.id, rank=new.trust_rank, held=old.trust_rank
    )
    return False


def conflicts_of(session: Session, owner_id: Any) -> list[tuple[MemoryItem, MemoryItem | None]]:
    """Claims waiting for the owner: (the claim, the fact it disagrees with, or None for a review item)."""
    claims = session.scalars(
        select(MemoryItem)
        .where(
            MemoryItem.subject_person_ids.contains([owner_id]),
            MemoryItem.conflict_status.in_(("pending", "review")),
            MemoryItem.valid_to.is_(None),
        )
        .order_by(MemoryItem.created_at.desc())
    ).all()
    out: list[tuple[MemoryItem, MemoryItem | None]] = []
    for c in claims:
        current = session.get(MemoryItem, c.conflicts_with_id) if c.conflicts_with_id else None
        out.append((c, current))
    return out


def resolve_conflict(
    session: Session, owner: Person, claim: MemoryItem, choice: str, text: str = ""
) -> MemoryItem | None:
    """The owner's answer, stored as a rank-1 fact. keep: the current fact stands and is confirmed;
    new: the claim is accepted; custom: the owner's own wording. Both older rows are kept, retired."""
    now = datetime.now(UTC)
    current = session.get(MemoryItem, claim.conflicts_with_id) if claim.conflicts_with_id else None
    claim.conflict_status, claim.valid_to = "resolved", now
    if choice == "keep":
        if current is not None and current.valid_to is None:
            current.trust_rank = trust.OWNER_VERIFIED
        audit(session, "memory", "conflict_resolved", memory_id=claim.id, choice="keep")
        return current
    content = " ".join((text if choice == "custom" else claim.content).split())[:2000]
    chosen = MemoryItem(
        content=content,
        subject_person_ids=list(claim.subject_person_ids),
        allowed_kind="persons",
        allowed_person_ids=[owner.id],
        min_trust=2,
        category=claim.category,
        source="owner_edit",
        trust_rank=trust.OWNER_VERIFIED,
        author_person_id=owner.id,
    )
    session.add(chosen)
    session.flush()
    claim.superseded_by = chosen.id
    if current is not None and current.valid_to is None:
        current.valid_to, current.superseded_by = now, chosen.id
    from superaide.memory.recall import vectors

    vectors.schedule(session, chosen)
    audit(session, "memory", "conflict_resolved", memory_id=claim.id, choice=choice, by=chosen.id)
    return chosen


def lock_document(session: Session, document_id: Any) -> Document | None:
    """The document, freshly read and locked until this transaction ends. Whoever saves facts for a document
    and whoever removes it both take this lock, so a removal can't slip between "is it still there?" and the
    insert of facts that would then outlive it (issue #87)."""
    return session.scalar(
        select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True)
    )


def retire_document(session: Session, document: Document) -> int:
    """Remove a shared file or link and every fact that came from it."""
    now = datetime.now(UTC)
    locked = lock_document(session, document.id)  # waits for facts being saved right now, then retires them too
    if locked is not None:
        document = locked
    document.removed_at = now
    n = rows_affected(
        session.execute(
            update(MemoryItem)
            .where(MemoryItem.document_id == document.id, MemoryItem.valid_to.is_(None))
            .values(valid_to=now)
        )
    )
    audit(session, "memory", "document_removed", document_id=document.id, facts_retired=n)
    return n


def preferences_for(session: Session, person: Person) -> OwnerPreference:
    pref = session.scalar(select(OwnerPreference).where(OwnerPreference.person_id == person.id))
    if pref is None:
        # Start in the time zone the browser reported at sign-up, until the owner says otherwise.
        account_tz = session.scalar(select(UserAccount.timezone).where(UserAccount.person_id == person.id))
        pref = OwnerPreference(person_id=person.id, timezone=account_tz or "UTC")
        session.add(pref)
        session.flush()
    return pref


def add_contact(
    session: Session,
    *,
    owner: Person,
    name: str,
    relationship: str = "other",
    vip: bool = False,
    notes: str = "",
    separate: bool = False,
) -> Contact:
    """Someone who matters to the owner. They become a Person (relation 'contact', no
    authority), so their Telegram or Slack can be linked to them later. With `separate`, a contact who
    shares the name is not reused: the owner said this is someone else (issue #93)."""
    name = " ".join(name.split())[:120]
    existing = (
        None
        if separate
        else session.scalar(
            select(Contact)
            .join(Person, Person.id == Contact.person_id)
            .where(
                Contact.owner_person_id == owner.id,
                Contact.removed_at.is_(None),
                # Case-insensitive equality, not ILIKE: in a name, % _ and \ are just characters.
                func.lower(Person.display_name) == name.lower(),
            )
        )
    )
    if existing is not None:
        existing.relationship = relationship or existing.relationship
        existing.vip = existing.vip or vip
        if notes:
            existing.notes = notes[:1000]
        return existing
    person = Person(display_name=name, relation="contact")
    session.add(person)
    session.flush()
    contact = Contact(
        owner_person_id=owner.id, person_id=person.id, relationship=relationship[:30], vip=vip, notes=notes[:1000]
    )
    session.add(contact)
    session.flush()
    audit(session, "people", "contact_added", contact_id=contact.id, vip=vip)
    return contact


def contacts_of(session: Session, owner: Person) -> list[tuple[Contact, Person]]:
    rows = session.execute(
        select(Contact, Person)
        .join(Person, Person.id == Contact.person_id)
        .where(Contact.owner_person_id == owner.id, Contact.removed_at.is_(None))
        .order_by(Contact.vip.desc(), Person.display_name)
    ).all()
    return [(c, p) for c, p in rows]
