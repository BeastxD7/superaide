"""A claimed full name that matches two or more contacts (issue #492, owner decision of 2 Nov).

Nothing is sent to anyone. The sender's reply is identical whether the name hit, was ambiguous or missed. The owner
gets one Needs-you card per candidate; yes on one sends a code to that contact only, no on all ignores the claim.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from superaide.approvals.models import Approval
from superaide.identity.linking import bootstrap_person
from superaide.identity.models import ChannelIdentity, LinkChallenge
from superaide.platform.db.session import session_scope
from superaide.policy.actions import resolve
from tests.conftest import OUR_TEAM, codes_sent, sl, tg

CLAIM = "I'm Priya Tan, I need the notes"
KIND = "identity_claim_pick"


@pytest.fixture
def two_priyas(manager, colleague):
    with session_scope() as s:
        bootstrap_person(s, name="Priya  Tan", relation="client", identities=[("slack", OUR_TEAM, "U_PRIYA_TWO")])


@pytest.fixture(autouse=True)
def _messengers(monkeypatch, outbox):
    from superaide.proactive import notify

    monkeypatch.setattr(notify, "messenger_for", lambda p: outbox.get(p))


def cards() -> list[Approval]:
    with session_scope() as s:
        return list(s.scalars(select(Approval).where(Approval.kind == KIND).order_by(Approval.created_at)).all())


def challenges() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(LinkChallenge)) or 0


def test_hit_ambiguous_and_miss_get_the_same_reply(agent, manager, colleague, outbox):
    hit = agent.handle(tg("880201", CLAIM))  # one Priya Tan: a code goes out
    with session_scope() as s:
        bootstrap_person(s, name="Priya Tan", relation="client", identities=[("slack", OUR_TEAM, "U_PRIYA_TWO")])
    ambiguous = agent.handle(tg("880202", CLAIM))
    miss = agent.handle(tg("880203", "I'm Zed Quill, I need the notes"))
    assert hit.mode == ambiguous.mode == miss.mode == "stranger"
    assert hit.reply == ambiguous.reply == miss.reply


def test_ambiguous_claim_sends_nothing_and_opens_one_card_per_candidate(agent, two_priyas, outbox):
    r = agent.handle(tg("880204", CLAIM))
    assert r.mode == "stranger"
    assert challenges() == 0 and codes_sent(outbox["slack"]) == [] and codes_sent(outbox["telegram"]) == []
    found = cards()
    assert len(found) == 2
    summaries = [a.payload["summary"] for a in found]
    assert all("Telegram" in x and "Priya Tan" in x and "2 of your contacts" in x for x in summaries)
    assert len({a.payload["person_id"] for a in found}) == 2 and all(a.status == "pending" for a in found)


def test_the_card_names_the_channel_it_came_from(agent, two_priyas):
    agent.handle(sl("U_STRANGER", CLAIM))
    assert all("Slack" in a.payload["summary"] for a in cards())


def test_asking_again_does_not_open_another_card(agent, two_priyas):
    agent.handle(tg("880205", CLAIM))
    agent.handle(tg("880205", CLAIM))
    assert len(cards()) == 2


def test_the_owner_picks_one_and_only_that_contact_is_sent_a_code(agent, two_priyas, outbox, manager):
    agent.handle(tg("880206", CLAIM))
    first, second = cards()
    with session_scope() as s:
        done = resolve(s, s.get(Approval, first.id), approve=True, decided_by="Jordan", via="app")
        assert "one-time code was sent" in done.payload["result"]
    assert challenges() == 1 and len(codes_sent(outbox["slack"])) == 1
    with session_scope() as s:
        other = s.get(Approval, second.id)
        assert other.status == "rejected" and other.decided_by.startswith("system")
        c = s.scalar(select(LinkChallenge))
        assert str(c.person_id) == first.payload["person_id"]
        stranger = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "880206"))
        assert stranger.person_id is None  # nothing is linked until they type the code


def test_ignoring_every_card_sends_nothing(agent, two_priyas, outbox):
    agent.handle(tg("880207", CLAIM))
    for a in cards():
        with session_scope() as s:
            resolve(s, s.get(Approval, a.id), approve=False, decided_by="Jordan", via="app")
    assert challenges() == 0 and codes_sent(outbox["slack"]) == []
    agent.handle(tg("880207", CLAIM))
    assert len(cards()) == 2  # a week's cooldown: the same account is not put to the owner again


def test_a_first_name_claim_never_opens_a_card(agent, two_priyas):
    agent.handle(tg("880208", "this is Priya, hi"))
    assert cards() == []


def test_a_stranger_cannot_decide_their_own_card(agent, two_priyas):
    from superaide.policy.actions import RULES

    assert KIND not in RULES  # the model and chat cannot propose it; only the app's approval routes decide it
