"""Mandate cards and attenuating tokens (issue #112)."""

from __future__ import annotations

import itertools
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from superaide.mandate import service
from superaide.mandate import token as tk
from superaide.mandate.service import Caps
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope

KEY = b"k" * 32
AGENT = str(uuid.uuid4())
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
LATER = (NOW + timedelta(days=2)).isoformat()


def root(**over):
    base = {"tools": ["create_event", "move_event"], "expires": LATER, "max_amount": 300}
    return tk.issue(KEY, agent_id=AGENT, issuer=uuid.uuid4(), **{**base, **over})


def ok(tok, tool="create_event", **args):
    return tk.authorize(KEY, tok, agent_id=AGENT, tool=tool, args=args, now=NOW)


def test_a_token_allows_what_the_card_says_and_nothing_else():
    tok = root()
    assert ok(tok).ok and ok(tok, "move_event").ok
    assert not ok(tok, "gmail_send").ok
    assert ok(tok, amount=300).ok and not ok(tok, amount=300.01).ok and not ok(tok, amount="lots").ok
    assert not tk.authorize(KEY, tok, agent_id=AGENT, tool="create_event", args={}, now=NOW + timedelta(days=3)).ok
    assert not tk.authorize(KEY, tok, agent_id=str(uuid.uuid4()), tool="create_event", args={}, now=NOW).ok
    assert "may use: create_event, move_event" in tk.card(tok) and "at most 300" in tk.card(tok)


def test_narrowing_works_offline_and_never_widens():
    tok = root()
    child = tk.narrow(tk.narrow(tok, "tools", ["create_event"]), "max_amount", 100)
    assert ok(child, amount=100).ok and not ok(child, "move_event").ok and not ok(child, amount=101).ok
    for name, wider in [("tools", ["create_event", "gmail_send"]), ("max_amount", 301),
                        ("expires", (NOW + timedelta(days=9)).isoformat())]:  # fmt: skip
        with pytest.raises(tk.MandateError):
            tk.narrow(tok, name, wider)
    with pytest.raises(tk.MandateError):
        tk.narrow(child, "tools", ["move_event"])  # not a subset of the narrowed list either


def test_a_forged_or_stripped_token_fails():
    tok = root()
    child = tk.narrow(tok, "tools", ["create_event"])
    # dropping the narrowing caveat keeps the old signature chain, so the signature no longer matches
    stripped = tk.Token(child.mandate_id, child.agent_id, child.issuer, tok.caveats, child.sig)
    assert not ok(stripped).ok
    forged = tk.Token(tok.mandate_id, tok.agent_id, tok.issuer, (*tok.caveats[:-1], ("max_amount", 9999)), tok.sig)
    assert not ok(forged).ok
    assert not tk.authorize(b"x" * 32, tok, agent_id=AGENT, tool="create_event", args={}, now=NOW).ok  # wrong key
    with pytest.raises(tk.MandateError):
        tk.loads("garbage")
    assert tk.loads(tk.dumps(child)) == child


def test_unknown_constraints_fail_closed_and_a_card_without_tools_or_expiry_allows_nothing():
    with pytest.raises(tk.MandateError):
        tk.narrow(root(), "geofence", "SG")
    # a verifier meets a caveat type it doesn't know (a newer client signed it): deny
    sig = tk._h(KEY, root().root())
    odd = tk.Token("i", AGENT, "o", (("tools", ["create_event"]), ("expires", LATER), ("geofence", "SG")), b"")
    for name, value in odd.caveats:
        sig = tk._h(sig, [name, value])
    odd = tk.Token(odd.mandate_id, odd.agent_id, odd.issuer, odd.caveats, sig)
    assert not tk.authorize(KEY, odd, agent_id=AGENT, tool="create_event", args={}, now=NOW).ok
    bare = tk.issue(KEY, agent_id=AGENT, issuer="o", tools=["create_event"])
    assert not ok(bare).ok and not ok(tk.issue(KEY, agent_id=AGENT, issuer="o")).ok


def test_recipient_domains_and_uses():
    tok = root(recipient_domains=["acme.com"], max_uses=2)
    assert ok(tok, recipients=["Dan@ACME.com"]).ok and not ok(tok, recipients=["x@evil.com"]).ok
    assert not tk.authorize(KEY, tok, agent_id=AGENT, tool="create_event", args={}, now=NOW, uses=2).ok


def test_random_attenuation_chains_never_gain_permission():
    """Property: whatever narrowing is applied, the child allows no call the parent refuses."""
    rng = random.Random(112)
    tools = ["create_event", "move_event", "gmail_send", "decline_event"]
    for _ in range(300):
        parent = root(tools=rng.sample(tools, 3), max_amount=rng.choice([50, 300]))
        child = parent
        for _ in range(rng.randint(1, 4)):
            try:
                child = tk.narrow(child, "tools", rng.sample(tools, rng.randint(1, 3)))
            except tk.MandateError:
                pass
            try:
                child = tk.narrow(child, "max_amount", rng.choice([10, 60, 400]))
            except tk.MandateError:
                pass
        for tool, amount in itertools.product(tools, [None, 5, 55, 350]):
            args = {} if amount is None else {"amount": amount}
            if ok(child, tool, **args).ok:
                assert ok(parent, tool, **args).ok


def test_rule_of_two_denies_all_three_together():
    for u, p, a in itertools.product([False, True], repeat=3):
        assert Caps(u, p, a).violates_rule_of_two == (u and p and a)


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setattr(get_settings(), "mandates_enabled", True)
    monkeypatch.setattr(service, "root_key", lambda: KEY)


def permit(s, tool="create_event", effect="write_external", **kw):
    caps = kw.pop("caps", Caps(untrusted_input=False, private_data=True, can_act=True))
    return service.permits(s, tool=tool, args=kw.pop("args", {}), effect=effect, caps=caps, now=NOW)


def test_the_gateway_honours_a_mandate_only_inside_its_ceiling(signed):
    with session_scope() as s:
        m = service.create(s, uuid.uuid4(), tools=["create_event", "gmail_send"], expires=NOW + timedelta(days=1),
                           max_uses=3)  # fmt: skip
        assert permit(s)
        assert not permit(s, "gmail_send", "send")  # a mandate never covers a send, whatever it says
        assert not permit(s, "decline_event", "write_external")
        assert not permit(s, caps=Caps(untrusted_input=True, private_data=True, can_act=True))
        assert permit(s) and permit(s) and not permit(s)  # 3 uses in all, then used up
        assert m.uses == 3 and m.card.startswith("may use")


def test_revoked_expired_or_disabled_mandates_do_nothing(signed, monkeypatch):
    with session_scope() as s:
        m = service.create(s, uuid.uuid4(), tools=["create_event"], expires=NOW + timedelta(days=1))
        assert permit(s)
        service.revoke(s, m.id)
        assert not permit(s)
        service.create(s, uuid.uuid4(), tools=["create_event"], expires=NOW + timedelta(hours=1))
        assert not service.permits(
            s, tool="create_event", args={}, effect="write_external", now=NOW + timedelta(hours=2),
            caps=Caps(False, True, True),
        )  # fmt: skip
        monkeypatch.setattr(get_settings(), "mandates_enabled", False)
        assert not permit(s)
