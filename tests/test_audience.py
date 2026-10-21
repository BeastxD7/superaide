from __future__ import annotations

from superaide.context.audience import Audience, visible_memories
from superaide.db.models import MemoryItem
from superaide.db.session import session_scope


def _add(content: str, kind: str, people=(), min_trust: int = 2) -> None:
    with session_scope() as s:
        s.add(MemoryItem(content=content, allowed_kind=kind, allowed_person_ids=sorted(people), min_trust=min_trust))


def _visible(aud: Audience) -> set[str]:
    with session_scope() as s:
        return {m.content for m in visible_memories(s, aud, limit=50)}


def test_audience_rules(manager, colleague):
    _add("manager private", "persons", [manager.id])
    _add("shared by both", "persons", [manager.id, colleague.id])
    _add("org wide", "org", [], min_trust=2)
    _add("public", "public", [], min_trust=0)

    manager_dm = Audience({manager.id}, has_unknown=False, min_trust=2, all_internal=True)
    assert _visible(manager_dm) == {"manager private", "shared by both", "org wide", "public"}

    colleague_dm = Audience({colleague.id}, has_unknown=False, min_trust=2, all_internal=True)
    assert _visible(colleague_dm) == {"shared by both", "org wide", "public"}

    both = Audience({manager.id, colleague.id}, has_unknown=False, min_trust=2, all_internal=True)
    assert _visible(both) == {"shared by both", "org wide", "public"}

    with_unknown = Audience({manager.id}, has_unknown=True, min_trust=0, all_internal=False)
    assert _visible(with_unknown) == {"public"}


def test_low_trust_sees_less(manager):
    _add("sensitive", "persons", [manager.id], min_trust=4)
    aud = Audience({manager.id}, has_unknown=False, min_trust=2, all_internal=True)
    assert "sensitive" not in _visible(aud)
