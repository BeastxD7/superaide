"""Import lease (#133): third-party cards held for a limited time, then erased.

The rules, all in code:
- The owner opens a lease for one source and a list of scopes, for 1 to 30 days (default 7).
- Cards come in as the card schema only (imports/cards.py). A card outside the lease's scopes,
  a duplicate of something Ava already knows, or one holding another person's e-mail address or
  phone number is left out and counted, never stored.
- Every imported row is the owner's alone (never shared), carries the lease id, and ranks lowest:
  confidence 0.4, source "imported". Nothing promotes it; it is not embedded (no model or vendor
  sees it) and it stops being recalled the moment the lease expires (valid_to is the lease end).
- Ending the lease, or its expiry, deletes every row carrying the lease id.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from superaide.features.imports.cards import (
    CATEGORIES,
    IMPORT_CONFIDENCE,
    MAX_CARDS_PER_LEASE,
    Card,
    LeaseRequest,
    has_contact_details,
)
from superaide.features.imports.models import ImportLease
from superaide.knowledge.store import find_same_fact
from superaide.platform.audit import audit
from superaide.platform.db.models import MemoryItem, Person
from superaide.platform.db.session import rows_affected


class LeaseError(Exception):
    """A request the lease rules refuse; the message is safe to show the owner."""


def open_lease(session: Session, owner: Person, req: LeaseRequest) -> ImportLease:
    lease = ImportLease(
        owner_person_id=owner.id,
        label=req.label,
        source_kind=req.source_kind,
        scopes=sorted(set(req.scopes)),
        expires_at=datetime.now(UTC) + timedelta(days=req.days),
    )
    session.add(lease)
    session.flush()
    audit(session, "import_lease", "opened", lease_id=lease.id, source=req.source_kind, scopes=lease.scopes)
    return lease


def _own(session: Session, owner: Person, lease_id: uuid.UUID) -> ImportLease:
    lease = session.get(ImportLease, lease_id)
    if lease is None or lease.owner_person_id != owner.id:
        raise LeaseError("No such import")
    return lease


def add_cards(session: Session, owner: Person, lease_id: uuid.UUID, cards: list[Card]) -> dict[str, int]:
    lease = _own(session, owner, lease_id)
    now = datetime.now(UTC)
    if lease.status != "active" or lease.expires_at <= now:
        raise LeaseError("This import has ended")
    if lease.card_count + len(cards) > MAX_CARDS_PER_LEASE:
        raise LeaseError(f"An import holds at most {MAX_CARDS_PER_LEASE} cards")

    skipped: Counter[str] = Counter()
    added = 0
    seen: set[str] = set()
    for card in cards:
        if card.scope not in lease.scopes:
            skipped["outside_scope"] += 1
            continue
        if has_contact_details(card.text) or has_contact_details(card.title):
            skipped["contact_details"] += 1
            continue
        content = f"{card.title}: {card.text}" if card.title else card.text
        key = content.lower()
        if key in seen or find_same_fact(session, owner, content, similarity=None) is not None:
            skipped["already_known"] += 1
            continue
        seen.add(key)
        session.add(
            MemoryItem(
                content=content,
                subject_person_ids=[owner.id],
                allowed_kind="persons",  # never from the file: imported cards are the owner's alone
                allowed_person_ids=[owner.id],
                min_trust=2,
                category=card.category if card.category in CATEGORIES else "other",
                source="imported",
                confidence=IMPORT_CONFIDENCE,
                valid_from=datetime.combine(card.when, datetime.min.time(), tzinfo=UTC) if card.when else now,
                valid_to=lease.expires_at,  # not recalled once the lease runs out, even before the sweep
                lease_id=lease.id,
            )
        )
        added += 1
    lease.card_count += added
    lease.skipped_count += sum(skipped.values())
    session.flush()
    audit(session, "import_lease", "cards_added", lease_id=lease.id, added=added, skipped=dict(skipped))
    return {"added": added, **{f"skipped_{k}": v for k, v in skipped.items()}}


def end_lease(session: Session, owner: Person, lease_id: uuid.UUID, *, status: str = "ended") -> int:
    """Erase every card of the lease. Returns how many rows went."""
    lease = _own(session, owner, lease_id)
    erased = rows_affected(session.execute(delete(MemoryItem).where(MemoryItem.lease_id == lease.id)))
    if lease.status == "active":
        lease.status = status
        lease.ended_at = datetime.now(UTC)
    audit(session, "import_lease", status, lease_id=lease.id, erased=erased)
    return erased


def sweep_expired(session: Session, owner: Person) -> int:
    """End every lease of this owner whose time is up. Called wherever leases are listed or opened."""
    now = datetime.now(UTC)
    due = session.scalars(
        select(ImportLease.id).where(
            ImportLease.owner_person_id == owner.id, ImportLease.status == "active", ImportLease.expires_at <= now
        )
    ).all()
    for lease_id in due:
        end_lease(session, owner, lease_id, status="expired")
    return len(due)


def view(session: Session, owner: Person) -> list[dict]:
    sweep_expired(session, owner)
    rows = session.scalars(
        select(ImportLease).where(ImportLease.owner_person_id == owner.id).order_by(ImportLease.created_at.desc())
    ).all()
    live = dict(
        session.execute(
            select(MemoryItem.lease_id, func.count())
            .where(MemoryItem.lease_id.is_not(None))
            .group_by(MemoryItem.lease_id)
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "label": r.label,
            "source_kind": r.source_kind,
            "scopes": r.scopes,
            "status": r.status,
            "expires_at": r.expires_at.isoformat(),
            "cards": r.card_count,
            "skipped": r.skipped_count,
            "cards_left": int(live.get(r.id, 0)),
        }
        for r in rows
    ]
