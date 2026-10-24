"""Who the airlock should treat as other people (issue #128): the owner's contacts and everyone else Ava knows,
read from the database at the moment of a model call. The owner's own name and address are kept: they are the
owner's to share with their own assistant's model."""

from __future__ import annotations

import logging

from sqlalchemy import select

from superaide.platform.db.models import Person

log = logging.getLogger(__name__)


def known_people() -> tuple[list[str], list[str]]:
    """(names to redact, owner values to keep) for the instance in scope. Never raises: with no database, or
    no owner yet, the pattern-based redaction still runs and no name is listed."""
    from superaide.app.accounts import owner as hired_owner
    from superaide.platform.db.session import session_scope
    from superaide.privacy.airlock import name_variants

    try:
        with session_scope() as s:
            me = hired_owner(s)
            mine = [] if me is None else [me.display_name]
            names = [
                p.display_name
                for p in s.scalars(select(Person).where(Person.status == "active", Person.kind == "human"))
                if me is None or p.id != me.id
            ]
        keep = name_variants(mine)
        return [n for n in name_variants(names) if n.casefold() not in {k.casefold() for k in keep}], keep
    except Exception:  # a model call must not fail because the contact list could not be read
        log.warning("airlock: couldn't read the people list", exc_info=True)
        return [], []
