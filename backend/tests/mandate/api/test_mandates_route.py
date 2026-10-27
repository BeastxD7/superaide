"""/app/mandates (#112): off for the owner until enabled, 401/403 for others, sign, hand on narrower, refuse widening,
revoke with children, and show the chain in words."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from tests.api_support import OTHER, signup


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    from superaide.identity.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"m" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def at(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def card(**over):
    return {
        "label": "Priya scheduling replies", "tools": ["create_event", "move_event"], "expires": at(7),
        "max_uses": 5, **over,
    }  # fmt: skip


def test_off_by_default_the_owner_gets_404(owner):
    assert owner.get("/app/mandates").status_code == 404
    assert owner.post("/app/mandates", json=card()).status_code == 404


def test_others_are_refused_first(browser, owner):
    assert browser().get("/app/mandates").status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/mandates").status_code == 403
    assert stranger.post("/app/mandates", json=card()).status_code == 403


@pytest.fixture
def on(settings, monkeypatch):
    monkeypatch.setattr(settings, "mandates_enabled", True)


def test_sign_hand_on_and_see_the_chain(owner, on):
    r = owner.post("/app/mandates", json=card())
    assert r.status_code == 200 and r.json()["depth"] == 0 and "5 time(s) at most" in r.json()["card"]
    pid = r.json()["id"]
    c = owner.post(
        f"/app/mandates/{pid}/delegate",
        json={"label": "sub-task", "max_uses": 3, "expires": at(2), "tools": ["create_event"]},
    )
    assert c.status_code == 200 and c.json()["depth"] == 1 and c.json()["parent_id"] == pid
    chain = c.json()["chain"]
    assert chain.startswith("Priya scheduling replies: may use: create_event, move_event") and " -> sub-task: " in chain
    assert "3 time(s) at most" in chain
    listing = owner.get("/app/mandates").json()["mandates"]
    assert [m["depth"] for m in listing] == [0, 1] and listing[1]["chain"] == chain


@pytest.mark.parametrize(
    "widen",
    [
        {"max_uses": 6},
        {"tools": ["create_event", "gmail_send"]},
        {"expires": at(30)},
        {"recipient_domains": ["evil.com"]},
    ],
)
def test_widening_is_refused_with_a_reason_and_nothing_is_made(owner, on, widen):
    pid = owner.post("/app/mandates", json=card(recipient_domains=["acme.com"])).json()["id"]
    r = owner.post(f"/app/mandates/{pid}/delegate", json={"label": "greedy", **widen})
    assert r.status_code == 422 and "can't" in r.json()["detail"]
    assert len(owner.get("/app/mandates").json()["mandates"]) == 1


def test_an_empty_or_unknown_narrowing_is_refused(owner, on):
    pid = owner.post("/app/mandates", json=card()).json()["id"]
    assert owner.post(f"/app/mandates/{pid}/delegate", json={"label": "same"}).status_code == 422
    assert owner.post(f"/app/mandates/{pid}/delegate", json={"label": "x", "geofence": "SG"}).status_code == 422


def test_revoking_the_parent_revokes_the_children(owner, on):
    pid = owner.post("/app/mandates", json=card()).json()["id"]
    cid = owner.post(f"/app/mandates/{pid}/delegate", json={"label": "c", "max_uses": 2}).json()["id"]
    assert owner.post(f"/app/mandates/{pid}/revoke").status_code == 200
    states = {m["id"]: m["status"] for m in owner.get("/app/mandates").json()["mandates"]}
    assert states == {pid: "revoked", cid: "revoked"}
    assert owner.post(f"/app/mandates/{cid}/delegate", json={"label": "late", "max_uses": 1}).status_code == 422
    assert owner.post("/app/mandates/00000000-0000-0000-0000-000000000000/revoke").status_code == 404


def test_a_card_needs_a_timezone_in_its_expiry(owner, on):
    naive = (datetime.now() + timedelta(days=2)).replace(tzinfo=None).isoformat()
    assert owner.post("/app/mandates", json=card(expires=naive)).status_code == 422
