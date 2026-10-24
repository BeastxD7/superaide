"""Principal Card (#135): a signed public page saying an outbound message is from an AI acting for the owner.
Off by default; forged, expired and revoked cards and the flag being off all answer the same 404; the page
shows nothing private; the signing key is never returned."""

from __future__ import annotations

import base64
import json

import pytest

from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import use_instance
from superaide.principal import card
from tests.api_support import OTHER, signup

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


def set_card(owner, first="Ananya", initial="T", **extra):
    r = owner.put("/app/principal-card", json={"first_name": first, "initial": initial, **extra})
    assert r.status_code == 200, r.text
    return r


def token_for(owner) -> str:
    from superaide.app.deps import _hired_instance  # the owner's instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        t = card.issue(s)
    assert t
    return t


def _user_id(s):
    from sqlalchemy import select

    from superaide.platform.db.models import UserAccount

    return s.scalar(select(UserAccount.id).order_by(UserAccount.created_at))


def _without_request_id(body: dict) -> dict:
    """The error envelope carries a per-response request id; everything else must be identical."""
    out = json.loads(json.dumps(body))
    if isinstance(out.get("error"), dict):
        out["error"].pop("request_id", None)
    return out


def test_the_global_flag_turns_every_route_into_a_404(browser, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    assert owner.get("/app/principal-card").status_code == 404
    assert owner.put("/app/principal-card", json={"first_name": "x"}).status_code == 404
    r = browser().get("/p/card/abc.def")
    assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"]
    with session_scope() as s:
        assert card.email_signature(s) is None and card.footer(s, channel="slack") == ""


def test_the_flag_is_on_by_default():
    assert get_settings().model_fields["principal_card_enabled"].default is True


def test_the_page_shows_the_card_and_nothing_private(browser, owner, on):
    set_card(owner, can=["Propose times"], cannot=["Agree prices"])
    r = browser().get(f"/p/card/{token_for(owner)}")
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is True and body["acts_for"] == "Ananya T."
    assert body["can"] == ["Propose times"] and body["cannot"] == ["Agree prices"]
    assert set(body) == {"ai", "agent", "acts_for", "can", "cannot", "valid_until", "notice"}
    assert "Bluebird" not in r.text and "jordan" not in r.text.lower() and "@" not in r.text


def test_the_card_is_on_by_default_using_the_first_name_on_the_account(owner, on):
    with session_scope() as s:
        sig = card.email_signature(s)
    assert sig is not None
    assert (
        "Regards,\nAva, personal assistant of Jordan" in sig["text"] and "AI assistant acting for Jordan" in sig["text"]
    )
    assert sig["label"] == "Sign as Ava, personal assistant of Jordan"


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
    from superaide.app.deps import _hired_instance

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


def test_the_sign_off_names_the_owner_and_links_the_card(owner, on):
    set_card(owner)
    from superaide.app.deps import _hired_instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        f = card.email_signature(s)["text"]
    assert "Ava, personal assistant of Ananya T." in f and "AI assistant acting for Ananya T." in f and "/c/" in f


def test_a_card_from_one_owner_cannot_be_checked_against_another(browser, owner, other, on):
    set_card(owner)
    t = token_for(owner)
    assert browser().get(f"/p/card/{t}").json()["acts_for"] == "Ananya T."
    assert other.get("/app/principal-card").status_code == 403  # not their instance


def test_the_signing_key_is_not_in_any_response(browser, owner, on):
    set_card(owner)
    texts = [owner.get("/app/principal-card").text, browser().get(f"/p/card/{token_for(owner)}").text]
    key = card._key()
    assert all(key.hex() not in t and base64.b64encode(key).decode() not in t for t in texts)


def test_owner_routes_refuse_everyone_else(browser, owner, on):
    anon = browser()
    assert anon.get("/app/principal-card").status_code == 401
    assert anon.put("/app/principal-card", json={"first_name": "x"}).status_code in (401, 403)
    assert anon.post("/app/principal-card/revoke").status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/principal-card").status_code == 403


def test_the_owner_gets_a_preview_link_that_opens_the_public_page(browser, owner, on):
    r = set_card(owner)
    token = r.json()["preview"].rsplit("/c/", 1)[1]
    assert browser().get(f"/p/card/{token}").json()["acts_for"] == "Ananya T."


def test_the_owner_sees_the_cards_sent_and_can_take_one_back(owner, on):
    set_card(owner, channels=True)
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


def test_sent_routes_are_off_with_the_flag(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    assert owner.get("/app/principal-card/sent").status_code == 404
    assert owner.post("/app/principal-card/sent/x/revoke", json={}).status_code == 404


# Opt-in, first name plus owner-chosen initial, and the experiment counters (#135).


def test_the_owner_can_switch_the_default_off_and_on_and_keeps_the_settings(owner, on):
    set_card(owner)
    assert owner.get("/app/principal-card").json()["sign_default"] is True
    r = owner.post("/app/principal-card/sign-default", json={"on": False})
    assert r.status_code == 200 and r.json()["sign_default"] is False and r.json()["active"] is False
    assert owner.get("/app/principal-card").json()["first_name"] == "Ananya"  # off keeps the settings
    assert owner.post("/app/principal-card/sign-default", json={"on": True}).json()["active"] is True


def test_a_legacy_opt_out_flag_in_state_does_not_switch_it_off(owner, on):
    from superaide.policy.killswitch import set_state

    with session_scope() as s:
        set_state(s, card.STATE_KEY, {"first_name": "Ananya", "opted_in": False})
    assert owner.get("/app/principal-card").json()["sign_default"] is True


def test_a_blank_first_name_falls_back_to_the_account_name(owner, on):
    r = owner.put("/app/principal-card", json={"first_name": ""})
    assert r.status_code == 200 and r.json()["name_shown"] == "Jordan"


@pytest.mark.parametrize("bad", ["Ananya Menon", "Ananya T.", "A1", "<b>x</b>", "x" * 41, " ", "-A"])
def test_a_full_name_or_junk_is_refused_as_first_name(owner, on, bad):
    r = owner.put("/app/principal-card", json={"first_name": bad})
    assert r.status_code == (200 if not bad.strip() else 422), bad


def test_the_name_shown_is_first_name_plus_the_chosen_initial_only(owner, on):
    assert set_card(owner, first="Meera", initial="i.").json()["display_name"] == "Meera I."
    assert set_card(owner, first="Meera", initial="").json()["display_name"] == "Meera"
    assert owner.put("/app/principal-card", json={"first_name": "Meera", "initial": "Iyer"}).status_code == 422
    assert set_card(owner, first="Meera", initial="7").json()["display_name"] == "Meera"  # not a letter: dropped


def test_stats_count_shown_opened_once_per_card_and_revoked(browser, owner, on):
    assert owner.get("/app/principal-card/stats").json() == {
        "shown": 0, "opened": 0, "revoked_one": 0, "revoked_all": 0, "open_rate": 0.0,
    }  # fmt: skip
    set_card(owner, channels=True)
    with session_scope() as s:
        card.footer(s, channel="slack", to="Priya Tan")
        card.footer(s, channel="telegram", to="Jon Lee")
    first = owner.get("/app/principal-card/sent").json()["cards"][1]
    from superaide.app.deps import _hired_instance

    with session_scope() as s:
        scope, _ = _hired_instance(_user_id(s))
    with use_instance(scope), session_scope() as s:
        tok = card.issue(s)
    anon = browser()
    assert anon.get(f"/p/card/{tok}").status_code == 200
    assert anon.get(f"/p/card/{tok}").status_code == 200  # opened twice, counted once
    assert anon.get("/p/card/forged.token").status_code == 404  # never counted
    owner.post(f"/app/principal-card/sent/{first['id']}/revoke", json={})
    owner.post("/app/principal-card/revoke", json={})
    st = owner.get("/app/principal-card/stats").json()
    assert (st["shown"], st["opened"], st["revoked_one"], st["revoked_all"]) == (2, 1, 1, 1)
    assert st["open_rate"] == 0.5


def test_stats_hold_no_recipients_or_text(owner, on):
    set_card(owner, channels=True)
    with session_scope() as s:
        card.footer(s, channel="slack", to="Priya Tan")
    assert "Priya" not in owner.get("/app/principal-card/stats").text


def test_stats_routes_are_off_with_the_flag(browser, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    assert owner.get("/app/principal-card/stats").status_code == 404
    assert owner.post("/app/principal-card/sign-default", json={"on": True}).status_code == 404


def test_stats_and_sign_default_refuse_everyone_else(browser, owner, on):
    anon = browser()
    assert anon.get("/app/principal-card/stats").status_code == 401
    assert anon.post("/app/principal-card/sign-default", json={"on": True}).status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/principal-card/stats").status_code == 403
    assert stranger.post("/app/principal-card/sign-default", json={"on": True}).status_code == 403


def test_wording_is_configurable_but_the_honest_line_and_link_are_fixed(owner, on):
    r = owner.put("/app/principal-card", json={"first_name": "Ananya", "wording": "Best,\n{agent} for {name}"})
    assert r.status_code == 200 and r.json()["wording"] == "Best,\n{agent} for {name}"
    with session_scope() as s:
        text = card.email_signature(s)["text"]
    assert "Best,\nAva for Ananya" in text and "is an AI assistant acting for Ananya" in text and "/c/" in text
    reset = owner.put("/app/principal-card", json={"first_name": "Ananya", "wording": "  "})
    assert reset.json()["wording"] == card.DEFAULT_WORDING


def test_telegram_and_slack_are_unaffected_unless_the_owner_turned_them_on(owner, on):
    set_card(owner)
    with session_scope() as s:
        assert card.footer(s, channel="slack", to="Priya Tan") == "" and card.sent(s) == []
    set_card(owner, channels=True)
    with session_scope() as s:
        assert "AI assistant acting for Ananya T." in card.footer(s, channel="slack", to="Priya Tan")
    owner.post("/app/principal-card/sign-default", json={"on": False})
    with session_scope() as s:
        assert card.footer(s, channel="slack", to="Priya Tan") == ""


# The signature block's settings and the live preview (#439).


def test_signature_settings_round_trip_with_safe_defaults(owner, on):
    cur = owner.get("/app/principal-card").json()
    assert (cur["avatar"], cur["mark"], cur["title"], cur["accent"]) == (True, False, "", "indigo")
    assert cur["accents"] == ["indigo", "teal", "slate", "rose", "amber"]
    r = owner.put(
        "/app/principal-card",
        json={
            "first_name": "Ananya",
            "avatar": False,
            "mark": True,
            "title": "Chief of staff to {name}",
            "accent": "rose",
        },
    )
    assert r.status_code == 200 and (r.json()["avatar"], r.json()["mark"], r.json()["accent"]) == (False, True, "rose")
    assert r.json()["title"] == "Chief of staff to {name}"


@pytest.mark.parametrize("bad", ["hotpink", "#ff0000", "", "indigo; background:url(x)"])
def test_an_accent_outside_the_palette_is_refused(owner, on, bad):
    assert owner.put("/app/principal-card", json={"first_name": "Ananya", "accent": bad}).status_code == 422


def test_the_preview_renders_unsaved_edits_and_changes_nothing(owner, on):
    r = owner.post("/app/principal-card/preview", json={"first_name": "Meera", "accent": "amber", "mark": True})
    assert r.status_code == 200
    body = r.json()
    assert "Personal assistant of Meera" in body["html"] and "#b45309" in body["html"] and "Sent with" in body["html"]
    assert "Verify this assistant" in body["html"] and "AI assistant acting for Meera" in body["text"]
    assert body["verify_url"].endswith("/c/Preview1")
    assert owner.get("/app/principal-card").json()["accent"] == "indigo"  # nothing was saved


def test_opening_settings_does_not_issue_a_new_link_each_time(owner, on):
    from sqlalchemy import func, select

    from superaide.principal.models import CardLink

    first = owner.get("/app/principal-card").json()["preview"]
    for _ in range(3):
        assert owner.get("/app/principal-card").json()["preview"] == first
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CardLink)) == 1
    owner.post("/app/principal-card/revoke", json={})  # after a revoke the preview link is a fresh one
    assert owner.get("/app/principal-card").json()["preview"] != first


def test_the_preview_refuses_a_bad_accent_and_everyone_else(owner, on, browser):
    assert owner.post("/app/principal-card/preview", json={"accent": "nope"}).status_code == 422
    assert browser().post("/app/principal-card/preview", json={}).status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.post("/app/principal-card/preview", json={}).status_code == 403


def test_the_preview_is_off_with_the_global_flag(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    assert owner.post("/app/principal-card/preview", json={}).status_code == 404
