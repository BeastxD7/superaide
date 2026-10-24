"""Footprint Guard (issue #136): what Ava creates in the world under the owner's name.

Two checks, both decided in code and both off unless FOOTPRINT_GUARD_ENABLED:
- creation kinds (accounts, profiles, public posts, pull requests, reviews) always wait for the owner and are
  logged when asked for;
- outbound text that names a person who is not one of the owner's contacts is flagged, so the approval card
  says so. The guard asks; it never silently drops.

The name check is deliberately simple (two or more capitalised words in a row, not at the start of a
sentence) and errs towards asking.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.db.models import Person
from superaide.policy.registry import CREATION_RULES

# Kinds whose text goes to or about someone else.
OUTBOUND_KINDS = frozenset({"message_someone_else", "publish_post", "post_review", "gmail_send"})
_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_STOP = {"Thank You", "Best Regards", "Kind Regards", "Dear Sir", "Good Morning", "Good Afternoon", "Happy Birthday"}


def enabled() -> bool:
    return get_settings().footprint_guard_enabled


def contact_names(session: Session) -> set[str]:
    rows = session.scalars(select(Person.display_name).where(Person.status == "active")).all()
    return {" ".join(r.lower().split()) for r in rows}


def unknown_names(session: Session, text: str) -> list[str]:
    """Capitalised multi-word names in `text` that match no contact (first name alone also counts as a match)."""
    known = contact_names(session)
    seen: list[str] = []
    for m in _NAME.finditer(text):
        name = m.group(1)
        low = " ".join(name.lower().split())
        if name in _STOP or any(k and k in low for k in known):
            continue
        if low not in (s.lower() for s in seen):
            seen.append(name)
    return seen[:5]


def assess(session: Session, *, kind: str, text: str, requester: Any = None) -> dict[str, Any]:
    """Extra approval-payload fields (empty when the guard is off or nothing was found); audits what it finds."""
    if not enabled():
        return {}
    out: dict[str, Any] = {}
    if kind in CREATION_RULES:
        out["footprint"] = "creates_in_the_world"
        audit(session, "policy", "footprint_creation", kind=kind, summary=text[:300])
    if kind in OUTBOUND_KINDS or kind in CREATION_RULES:
        names = unknown_names(session, text)
        if names:
            out["names_outside_contacts"] = names
            audit(session, "policy", "footprint_unknown_names", kind=kind, count=len(names))
    return out
