"""Narrowing keys (#112): a mandate handed on can only be narrower, a child is verified through its whole chain at
execution, revoking a parent revokes the children, and the owner sees the chain in words."""

from __future__ import annotations

import itertools
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from superaide.config import get_settings
from superaide.db.models import Mandate
from superaide.db.session import session_scope
from superaide.mandate import service
from superaide.mandate import token as tk
from superaide.mandate.service import Caps

KEY = b"k" * 32
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
CAPS = Caps(untrusted_input=False, private_data=True, can_act=True)


@pytest.fixture(autouse=True)
def signed(monkeypatch):
    monkeypatch.setattr(get_settings(), "mandates_enabled", True)
    monkeypatch.setattr(service, "root_key", lambda: KEY)


def parent(s, **over):
    base = dict(
        tools=["create_event", "move_event", "draft_reply"],
        expires=NOW + timedelta(days=7),
        max_uses=5,
        recipient_domains=["acme.com", "example.org"],
        topics=["scheduling", "invoices"],
        label="Priya scheduling replies",
    )
    return service.create(s, uuid.uuid4(), **{**base, **over})


def permit(s, tool="create_event", **args):
    return service.permits(s, tool=tool, args=args, effect="write_external", caps=CAPS, now=NOW)


def test_a_child_with_fewer_tools_a_shorter_expiry_a_lower_count_and_fewer_recipients_works(signed):
    with session_scope() as s:
        p = parent(s)
        c = service.delegate(
            s,
            p.id,
            label="sub-task",
            now=NOW,
            tools=["create_event"],
            expires=(NOW + timedelta(days=2)).isoformat(),
            max_uses=3,
            recipient_domains=["acme.com"],
            topics=["scheduling"],
        )
        assert c.parent_id == p.id and c.expires_at == NOW + timedelta(days=2)
        assert tk.never_wider(tk.loads(p.token), tk.loads(c.token))


@pytest.mark.parametrize(
    "widen",
    [
        {"tools": ["create_event", "gmail_send"]},
        {"expires": (NOW + timedelta(days=30)).isoformat()},
        {"max_uses": 6},
        {"recipient_domains": ["acme.com", "evil.com"]},
        {"topics": ["scheduling", "payroll"]},
    ],
)
def test_a_child_can_never_widen_and_nothing_is_stored(widen):
    with session_scope() as s:
        p = parent(s)
        before = len(s.query(Mandate).all())
        with pytest.raises(tk.MandateError):
            service.delegate(s, p.id, label="greedy", now=NOW, **widen)
        assert len(s.query(Mandate).all()) == before


def test_an_unknown_limit_and_an_empty_delegation_are_refused():
    with session_scope() as s:
        p = parent(s)
        with pytest.raises(tk.MandateError):
            service.delegate(s, p.id, label="x", now=NOW, geofence="SG")
        with pytest.raises(tk.MandateError):
            service.delegate(s, p.id, label="x", now=NOW)


def test_a_session_that_read_untrusted_content_cannot_hand_out_authority():
    with session_scope() as s:
        p = parent(s)
        with pytest.raises(tk.MandateError):
            service.delegate(s, p.id, label="x", now=NOW, caps=Caps(True, True, True), max_uses=1)


def test_handing_on_is_limited_in_depth():
    with session_scope() as s:
        p = parent(s)
        c1 = service.delegate(s, p.id, label="a", now=NOW, max_uses=4)
        c2 = service.delegate(s, c1.id, label="b", now=NOW, max_uses=3)
        with pytest.raises(tk.MandateError):
            service.delegate(s, c2.id, label="c", now=NOW, max_uses=2)


def test_a_helper_holding_a_child_gets_only_the_childs_reach_and_its_calls_use_up_the_parent():
    with session_scope() as s:
        p = parent(s)
        c = service.delegate(s, p.id, label="sub-task", now=NOW, tools=["create_event"], max_uses=2)

        def via_child(tool, **args):
            return service.permits(
                s, tool=tool, args=args, effect="write_external", caps=CAPS, now=NOW, mandate_id=c.id
            )

        assert not via_child("move_event")  # the parent covers it, the helper's child does not
        assert via_child("create_event") and via_child("create_event")
        assert not via_child("create_event")  # the child's own count (2) is used up
        s.refresh(p)
        assert p.uses == 2  # and both calls came out of the parent's 5


def test_the_total_of_a_family_never_exceeds_the_parents_count():
    with session_scope() as s:
        p = parent(s, max_uses=3)
        service.delegate(s, p.id, label="a", now=NOW, max_uses=3)
        service.delegate(s, p.id, label="b", now=NOW, max_uses=3)
        granted = sum(permit(s, "create_event") for _ in range(10))
        assert granted == 3  # not 3 + 3 + 3


def test_revoking_the_parent_revokes_every_descendant_and_they_stop_working():
    with session_scope() as s:
        p = parent(s)
        c = service.delegate(s, p.id, label="child", now=NOW, tools=["create_event"])
        g = service.delegate(s, c.id, label="grandchild", now=NOW, max_uses=1)
        service.revoke(s, p.id)
        assert {r.status for r in (p, c, g)} == {"revoked"}
        assert not permit(s)


def test_revoking_a_child_leaves_the_parent_alone():
    with session_scope() as s:
        p = parent(s)
        c = service.delegate(s, p.id, label="child", now=NOW, tools=["create_event"])
        service.revoke(s, c.id)
        assert p.status == "active" and c.status == "revoked" and permit(s, "move_event")


def test_a_child_whose_parent_expired_or_is_gone_is_dead():
    with session_scope() as s:
        p = parent(s, expires=NOW + timedelta(hours=2))
        service.delegate(s, p.id, label="child", now=NOW, tools=["create_event"])
        later = NOW + timedelta(hours=3)
        assert not service.permits(s, tool="create_event", args={}, effect="write_external", caps=CAPS, now=later)


def test_a_tampered_chain_is_refused_at_execution():
    """A child row whose token is not its parent's token plus restrictions (swapped in the database) does not run,
    even if its own signature is valid."""
    with session_scope() as s:
        p = parent(s, tools=["create_event"], max_uses=1)
        other = service.create(s, uuid.uuid4(), tools=["create_event", "move_event"], expires=NOW + timedelta(days=9))
        forged = Mandate(token=other.token, card=other.card, expires_at=other.expires_at, parent_id=p.id, label="f")
        s.add(forged)
        s.flush()
        service.revoke(s, other.id)  # only the forged child could possibly cover move_event
        assert not permit(s, "move_event")


def test_a_send_or_a_spend_is_never_covered_even_through_a_chain():
    with session_scope() as s:
        p = parent(s, tools=["gmail_send"])
        service.delegate(s, p.id, label="c", now=NOW, max_uses=1)
        assert not service.permits(s, tool="gmail_send", args={}, effect="send", caps=CAPS, now=NOW)
        assert not service.permits(s, tool="gmail_send", args={}, effect="spend", caps=CAPS, now=NOW)


def test_random_delegations_never_gain_permission_over_the_parent():
    rng = random.Random(1120)
    tools = ["create_event", "move_event", "draft_reply", "decline_event"]
    key = KEY
    agent = str(uuid.uuid4())
    for _ in range(300):
        top = tk.issue(
            key,
            agent_id=agent,
            issuer="o",
            tools=rng.sample(tools, 3),
            expires=(NOW + timedelta(days=5)).isoformat(),
            max_uses=rng.choice([2, 5]),
            max_amount=rng.choice([50, 300]),
        )
        child = top
        for _ in range(rng.randint(1, 3)):
            for name, val in [
                ("tools", rng.sample(tools, rng.randint(1, 3))),
                ("max_amount", rng.choice([10, 60, 400])),
                ("max_uses", rng.choice([1, 3, 9])),
                ("expires", (NOW + timedelta(days=rng.choice([1, 4, 9]))).isoformat()),
            ]:
                try:
                    child = tk.narrow(child, name, val)
                except tk.MandateError:
                    pass
        assert tk.never_wider(top, child)
        for tool, amount, uses, days in itertools.product(tools, [None, 5, 55, 350], [0, 1, 4], [0, 3, 8]):
            args = {} if amount is None else {"amount": amount}
            when = NOW + timedelta(days=days)
            c_ok = tk.authorize(key, child, agent_id=agent, tool=tool, args=args, now=when, uses=uses).ok
            if c_ok:
                assert tk.authorize(key, top, agent_id=agent, tool=tool, args=args, now=when, uses=uses).ok


def test_the_owner_sees_the_chain_in_words():
    with session_scope() as s:
        p = parent(s)
        c = service.delegate(
            s, p.id, label="sub-task", now=NOW, max_uses=3, expires=(NOW + timedelta(days=2)).isoformat()
        )
        view = service.chain_view(s, c)
        assert view["depth"] == 1 and view["parent_id"] == str(p.id)
        assert view["chain"].startswith("Priya scheduling replies: may use: create_event")
        assert (
            " -> sub-task: " in view["chain"]
            and "3 time(s) at most" in view["chain"]
            and "until 2026-10-25" in view["chain"]
        )


def test_topics_are_enforced():
    with session_scope() as s:
        p = parent(s)
        assert permit(s, "create_event", topic="Scheduling")
        assert not permit(s, "create_event", topic="payroll")
        assert p.uses == 1
