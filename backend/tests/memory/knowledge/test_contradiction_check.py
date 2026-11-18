"""Two notes that disagree about a single-valued attribute are not both served (#555)."""

from __future__ import annotations

import pytest

from superaide.identity.models import Person
from superaide.memory.knowledge.store import find_contradicted_fact, remember_fact
from superaide.platform.db.session import session_scope


@pytest.mark.parametrize(
    ("old", "new", "hit"),
    [
        ("Jordan lives in Mumbai.", "Jordan lives in Pune.", True),
        ("Jordan lives in Pune.", "Jordan lives in Pune.", False),
        ("Jordan works at Orchard Labs.", "Jordan works at Fernhill Studio.", True),
        ("Jordan lives in Mumbai.", "Priya Tan lives in Pune.", False),
        ("Jordan likes pizza.", "Jordan likes tea.", False),
        ("Jordan lives in Mumbai.", "Jordan works at Orchard Labs.", False),
    ],
)
def test_only_a_different_value_for_the_same_subject_and_attribute_contradicts(agent, old, new, hit):
    with session_scope() as s:
        owner = Person(display_name="Jordan Whitfield", relation="unknown")
        s.add(owner)
        s.flush()
        before = remember_fact(s, owner=owner, content=old)
        assert before is not None
        found = find_contradicted_fact(s, owner, new)
        assert (found is not None) is hit
        if hit:
            assert found is before
