"""Two first messages from one new sender/chat at once: the loser of the insert race is not dropped (BE-14, #91)."""

from __future__ import annotations

from sqlalchemy import func, select

from superaide.identity import resolver
from superaide.identity.models import ChannelIdentity
from superaide.memory import store
from superaide.memory.models import Context
from superaide.platform.db.session import session_scope
from tests.conftest import tg


def test_losing_the_identity_insert_race_uses_the_winners_row(monkeypatch):
    msg = tg("777", "Hi")
    with session_scope() as s:
        resolver.resolve(s, msg)  # the winner has already inserted
    real = resolver.find_identity
    calls = []

    def blind_first(*a, **k):
        calls.append(1)
        return None if len(calls) == 1 else real(*a, **k)  # the loser looked before the winner committed

    monkeypatch.setattr(resolver, "find_identity", blind_first)
    with session_scope() as s:
        sender = resolver.resolve(s, tg("777", "it's about the invoice"))
        assert sender.identity is not None
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(ChannelIdentity).where(ChannelIdentity.stable_key == "777")) == 1
        )


def test_losing_the_context_insert_race_uses_the_winners_row(monkeypatch):
    msg = tg("778", "Hi")
    with session_scope() as s:
        first = store.get_or_create_context(s, msg)
        first_id = first.id
    real = store._find_context
    calls = []

    def blind_first(*a, **k):
        calls.append(1)
        return None if len(calls) == 1 else real(*a, **k)

    monkeypatch.setattr(store, "_find_context", blind_first)
    with session_scope() as s:
        again = store.get_or_create_context(s, tg("778", "and another"))
        assert again.id == first_id
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Context).where(Context.external_id == msg.chat_id)) == 1
