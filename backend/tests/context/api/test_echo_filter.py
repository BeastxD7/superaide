"""Echo Filter (#137): ten copies of one rumour count once; two independent roots, or the owner, make a fact."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.knowledge.store import remember_fact
from superaide.memory import echo
from superaide.platform.db.models import AuditLog, MemoryItem, Person
from superaide.platform.db.session import session_scope

RUMOUR = "The Acme merger closes in March."


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "echo_filter_enabled", True)


def state(content: str = RUMOUR) -> tuple[str, int]:
    with session_scope() as s:
        m = s.scalar(select(MemoryItem).where(MemoryItem.content == content))
        return m.belief_state, echo.roots(s, m)


def hear(owner_id, *, url=None, author=None, chain=(), source="link", content=RUMOUR):
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        remember_fact(
            s, owner=owner, content=content, source=source, origin_url=url, origin_author=author, quote_chain=chain
        )


def test_canonical_url_ignores_tracking_www_and_slashes():
    a = echo.origin_root(url="https://www.news.example/story/1/?utm_source=x&b=2#top")
    b = echo.origin_root(url="http://news.example/story/1?b=2&fbclid=zz")
    assert a == b
    assert a != echo.origin_root(url="https://other.example/story/1?b=2")


def test_a_quote_chain_collapses_to_its_oldest_link():
    one = echo.origin_root(quote_chain=["https://a.example/fwd", "https://b.example/quote", "https://c.example/root"])
    two = echo.origin_root(quote_chain=["https://d.example/other", "https://c.example/root/"])
    assert one == two
    assert echo.origin_root() is None


def test_ten_copies_of_one_rumour_stay_hearsay(manager, on):
    for i in range(10):
        hear(manager.id, url=f"https://wire.example/acme?utm_campaign={i}", source="link" if i % 2 else "telegram")
    assert state() == ("hearsay", 1)
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "hearsay_promoted")) is None


def test_a_second_independent_origin_promotes_it(manager, on):
    hear(manager.id, url="https://wire.example/acme")
    hear(manager.id, url="https://wire.example/acme/")  # same page again
    assert state() == ("hearsay", 1)
    hear(manager.id, author="Priya Tan", source="slack")  # someone else saw it first-hand
    assert state() == ("fact", 2)


def test_repeats_through_a_forwarded_chain_of_one_root_do_not_promote(manager, on):
    hear(manager.id, chain=("https://x.example/fwd1", "https://root.example/a"), source="telegram")
    hear(manager.id, chain=("https://y.example/fwd2", "https://z.example/q", "https://root.example/a"), source="slack")
    assert state() == ("hearsay", 1)


def test_the_owner_can_confirm(owner, on):
    hear_id = None
    with session_scope() as s:
        from superaide.platform.db.models import UserAccount

        pid = s.scalar(select(UserAccount.person_id))
        hear_id = pid
    hear(hear_id, url="https://wire.example/acme")
    with session_scope() as s:
        mid = s.scalar(select(MemoryItem.id).where(MemoryItem.content == RUMOUR))
    assert owner.get("/app/notebook").json()["memories"][0]["hearsay"] is True
    assert owner.post(f"/app/notebook/{mid}/confirm").json() == {"status": "fact"}
    assert state()[0] == "fact"


def test_first_hand_sources_are_never_hearsay(manager, on):
    hear(manager.id, source="chat", url="https://wire.example/acme")
    assert state() == ("fact", 0)


def test_off_by_default_nothing_changes(manager):
    hear(manager.id, url="https://wire.example/acme")
    assert state() == ("fact", 0)


def test_hearsay_is_labelled_when_recalled(manager, on):
    from superaide.context.audience import Audience
    from superaide.context.recall import recall_memories

    hear(manager.id, url="https://wire.example/acme")
    with session_scope() as s:
        found = recall_memories(s, Audience(person_ids={manager.id}, min_trust=2), "Acme merger", limit=5)
    assert found and "hearsay" in found[0].title


def test_pollution_replay_with_and_without_origin_dedupe(manager, on):
    """The issue's experiment: 3 rumours each echoed 20 times (reposts of one root), plus 1 true item that
    arrives from two roots. Counting repeats would promote all 4; counting origins promotes only the true one."""
    rumours = [f"Rumour number {i} about the Acme board." for i in range(3)]
    truth = "The Acme board meets on 3 December."
    for i, r in enumerate(rumours):
        for k in range(20):
            hear(manager.id, chain=(f"https://feed.example/repost/{k}", f"https://origin.example/{i}"), content=r)
    hear(manager.id, url="https://wire.example/board", content=truth)
    hear(manager.id, author="Priya Tan", source="slack", content=truth)
    assert [c for c in [*rumours, truth] if state(c)[0] == "fact"] == [truth]
    assert all(state(r) == ("hearsay", 1) for r in rumours)
    repeats = 20  # a naive counter would see 20 copies of each rumour and promote it
    assert repeats >= echo.PROMOTE_AT
