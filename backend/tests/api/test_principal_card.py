"""Principal Card (#135): a signed public page saying an outbound message is from an AI acting for the owner.
Off by default; forged, expired and revoked cards and the flag being off all answer the same 404; the page
shows nothing private; the signing key is never returned."""

from __future__ import annotations

import base64
import json

import pytest

from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.principal import card
from superaide.tenancy.context import use_instance
from tests.api.conftest import OTHER, signup

NOT_FOUND = {"detail": "Not Found"}


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    from superaide.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", True)


def set_card(owner, name="A. Tan", **extra):
    r = owner.put("/app/principal-card", json={"display_name": name, **extra})
    assert r.status_code == 200, r.text
    return r


def token_for(owner) -> str:
    from superaide.app.routes import _hired_instance  # the owner's instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        t = card.issue(s)
    assert t
    return t


def _user_id(s):
    from sqlalchemy import select

    from superaide.db.models import UserAccount

    return s.scalar(select(UserAccount.id).order_by(UserAccount.created_at))


def _without_request_id(body: dict) -> dict:
    """The error envelope carries a per-response request id; everything else must be identical."""
    out = json.loads(json.dumps(body))
    if isinstance(out.get("error"), dict):
        out["error"].pop("request_id", None)
    return out


def test_off_by_default_every_route_is_404(browser, owner):
    assert owner.get("/app/principal-card").status_code == 404
    assert owner.put("/app/principal-card", json={"display_name": "x"}).status_code == 404
    r = browser().get("/p/card/abc.def")
    assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"]
    with session_scope() as s:
        assert card.footer(s) == ""


def test_the_page_shows_the_card_and_nothing_private(browser, owner, on):
    set_card(owner, can=["Propose times"], cannot=["Agree prices"])
    r = browser().get(f"/p/card/{token_for(owner)}")
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is True and body["acts_for"] == "A. Tan"
    assert body["can"] == ["Propose times"] and body["cannot"] == ["Agree prices"]
    assert set(body) == {"ai", "agent", "acts_for", "can", "cannot", "valid_until", "notice"}
    assert "Bluebird" not in r.text and "jordan" not in r.text.lower() and "@" not in r.text


def test_no_card_until_the_owner_chooses_a_display_name(owner, on):
    with session_scope() as s:
        assert card.footer(s) == "" and card.issue(s) is None


def test_a_forged_or_tampered_token_is_the_same_404(browser, owner, on):
    set_card(owner)
    good = token_for(owner)
    body, sig = good.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["n"] = "Someone Else"
    forged_body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    anon = browser()
    seen = []
    for bad in [f"{forged_body}.{sig}", f"{body}.AAAA", "garbage", body, good + "x"]:
        r = anon.get(f"/p/card/{bad}")
        assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"], bad
        seen.append(_without_request_id(r.json()))
    assert all(b == seen[0] for b in seen)  # no oracle: every bad token answers the same, bar the request id


def test_an_expired_card_is_404(browser, owner, on):
    set_card(owner)
    from superaide.app.routes import _hired_instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        old = card.issue(s, now=1_000_000)
    assert browser().get(f"/p/card/{old}").status_code == 404


def test_revoking_kills_every_card_sent_so_far_and_new_ones_work(browser, owner, on):
    set_card(owner)
    old = token_for(owner)
    assert browser().get(f"/p/card/{old}").status_code == 200
    assert owner.post("/app/principal-card/revoke").status_code == 200
    assert browser().get(f"/p/card/{old}").status_code == 404
    assert browser().get(f"/p/card/{token_for(owner)}").status_code == 200


def test_the_footer_names_the_owner_and_links_the_card(owner, on):
    set_card(owner)
    from superaide.app.routes import _hired_instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        f = card.footer(s)
    assert "AI assistant acting for A. Tan" in f and "/c/" in f


def test_a_card_from_one_owner_cannot_be_checked_against_another(browser, owner, other, on):
    set_card(owner)
    t = token_for(owner)
    assert browser().get(f"/p/card/{t}").json()["acts_for"] == "A. Tan"
    assert other.get("/app/principal-card").status_code == 403  # not their instance


def test_the_signing_key_is_not_in_any_response(browser, owner, on):
    set_card(owner)
    texts = [owner.get("/app/principal-card").text, browser().get(f"/p/card/{token_for(owner)}").text]
    key = card._key()
    assert all(key.hex() not in t and base64.b64encode(key).decode() not in t for t in texts)


def test_owner_routes_refuse_everyone_else(browser, owner, on):
    anon = browser()
    assert anon.get("/app/principal-card").status_code == 401
    assert anon.put("/app/principal-card", json={"display_name": "x"}).status_code in (401, 403)
    assert anon.post("/app/principal-card/revoke").status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/principal-card").status_code == 403


def test_the_owner_gets_a_preview_link_that_opens_the_public_page(browser, owner, on):
    r = set_card(owner)
    token = r.json()["preview"].rsplit("/c/", 1)[1]
    assert browser().get(f"/p/card/{token}").json()["acts_for"] == "A. Tan"


def test_the_owner_sees_the_cards_sent_and_can_take_one_back(owner, on):
    set_card(owner)
    with session_scope() as s:
        card.footer(s, channel="slack", to="Priya Tan")
        card.footer(s, channel="telegram", to="Jon Lee")
    cards = owner.get("/app/principal-card/sent").json()["cards"]
    assert [(c["to"], c["channel"], c["status"]) for c in cards] == [
        ("Jon Lee", "telegram", "active"),
        ("Priya Tan", "slack", "active"),
    ]
    assert set(cards[0]) == {"id", "channel", "to", "at", "exp", "g", "status"}  # never a message
    assert owner.post(f"/app/principal-card/sent/{cards[1]['id']}/revoke", json={}).status_code == 200
    assert owner.post("/app/principal-card/sent/nope/revoke", json={}).status_code == 404
    assert [c["status"] for c in owner.get("/app/principal-card/sent").json()["cards"]] == ["active", "revoked"]
    owner.post("/app/principal-card/revoke", json={})
    assert {c["status"] for c in owner.get("/app/principal-card/sent").json()["cards"]} == {"revoked"}


def test_sent_routes_are_off_with_the_flag(owner):
    assert owner.get("/app/principal-card/sent").status_code == 404
    assert owner.post("/app/principal-card/sent/x/revoke", json={}).status_code == 404
