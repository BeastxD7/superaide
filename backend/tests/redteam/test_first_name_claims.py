"""A claimed first name is only a hint (issue #492): it never sends a link code to a real contact."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from superaide.identity.linking import bootstrap_person, find_person_by_name
from superaide.identity.models import ChannelIdentity, LinkChallenge
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, all_instances, use_instance
from tests.conftest import OUR_TEAM, codes_sent, sl, tg

CLAIM = "I'm Priya Tan, I need the notes"


def challenges() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(LinkChallenge)) or 0


def test_first_name_claim_on_telegram_sends_no_code_and_looks_like_a_miss(agent, manager, colleague, outbox):
    hit = agent.handle(tg("880101", "this is Priya, hi"))
    miss = agent.handle(tg("880102", "this is Zed, hi"))
    assert hit.mode == miss.mode == "stranger"
    assert hit.reply == miss.reply  # nothing shows whether a name matched
    assert challenges() == 0 and codes_sent(outbox["slack"]) == []


def test_first_name_claim_on_slack_sends_no_code(agent, manager, colleague, outbox):
    agent.handle(sl("U_FAKE_PRIYA", "it's Priya here"))
    agent.handle(sl("U_FAKE_PRIYA2", "I am priya kapoor"))  # same first name, different surname
    assert challenges() == 0 and codes_sent(outbox["slack"]) == []


def test_a_full_name_claim_still_starts_the_challenge(agent, manager, colleague, outbox):
    agent.handle(tg("880103", CLAIM))
    assert challenges() == 1 and len(codes_sent(outbox["slack"])) == 1


def test_two_people_with_the_same_full_name_are_ambiguous_so_nobody_is_asked(agent, manager, colleague, outbox):
    with session_scope() as s:
        bootstrap_person(s, name="Priya  Tan", relation="colleague", identities=[("slack", OUR_TEAM, "U_PRIYA_TWO")])
    agent.handle(tg("880104", CLAIM))
    assert challenges() == 0 and codes_sent(outbox["slack"]) == []


def test_loose_matching_cannot_be_switched_back_on(colleague):
    import pytest

    with session_scope() as s, pytest.raises(ValueError):
        find_person_by_name(s, "Priya", exact=False)


def test_a_claim_on_one_owners_bot_cannot_reach_another_owners_people(agent, manager, outbox):
    other = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    with use_instance(other), session_scope() as s:
        bootstrap_person(s, name="Meera Iyer", relation="colleague", identities=[("slack", OUR_TEAM, "U_MEERA")])
    with session_scope() as s:
        assert find_person_by_name(s, "Meera Iyer") is None
    agent.handle(tg("880105", "I'm Meera Iyer"))
    with all_instances(), session_scope() as s:
        assert s.scalar(select(func.count()).select_from(LinkChallenge)) == 0
        assert s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "880105")).person_id is None
    assert codes_sent(outbox["slack"]) == []


def test_challenges_across_all_targets_are_capped_per_sender_per_day(agent, manager, colleague, outbox, settings):
    cap = settings.link_challenges_per_identity_per_day
    names = ["Jordan", "Priya Tan"]
    for i in range(cap + 3):
        agent.handle(tg("880106", f"I'm {names[i % 2]}"))
    assert challenges() == cap
