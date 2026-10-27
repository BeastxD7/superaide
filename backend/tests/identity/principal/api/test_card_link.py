"""Short Principal Card links (#438): the link in an email uses the public address and is short, opens the card
from the owner's current settings, dies with revoke and expiry, and old long tokens keep working. Every outward link
is built by one helper (a guard fails on new `frontend_origin` uses in link-building code)."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from superaide import links
from superaide.identity.principal import card
from superaide.identity.principal.models import CardLink
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import use_instance

NOT_FOUND = {"detail": "Not Found"}


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    from superaide.identity.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


@pytest.fixture
def public(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "public_base_url", "https://example.test")
    monkeypatch.setattr(s, "frontend_origin", "http://localhost:3000")
    monkeypatch.setattr(s, "principal_card_enabled", True)


def _scope(owner):
    from sqlalchemy import select as sel

    from superaide.app.deps import _hired_instance
    from superaide.platform.db.models import UserAccount

    with session_scope() as s:
        scope, _ = _hired_instance(s.scalar(sel(UserAccount.id).order_by(UserAccount.created_at)))
    return scope


def new_link(owner) -> dict:
    with use_instance(_scope(owner)), session_scope() as s:
        link = card.issue_link(s)
    assert link
    return link


def test_the_link_uses_the_public_address_not_localhost_and_is_short(owner, public):
    with use_instance(_scope(owner)), session_scope() as s:
        sig = card.email_signature(s)
    assert sig is not None
    url = sig["url"]
    assert url.startswith("https://example.test/c/") and "localhost" not in sig["text"]
    assert len(url) <= 60 and re.fullmatch(r"https://example\.test/c/[A-Za-z0-9_-]{8}", url)
    assert url in sig["text"]


def test_with_no_public_address_the_frontend_origin_is_the_fallback(owner, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "public_base_url", "")
    monkeypatch.setattr(s, "frontend_origin", "https://app.example.test/")
    assert (
        links.public_url("/c/x") == "https://app.example.test/c/x" and links.public_url() == "https://app.example.test"
    )


def test_a_short_link_opens_the_card_from_the_current_settings(owner, browser, public):
    link = new_link(owner)
    r = browser().get(f"/p/card/{link['code']}")
    assert r.status_code == 200 and r.json()["acts_for"] == "Jordan" and r.json()["valid_until"] == link["exp"]
    owner.put("/app/principal-card", json={"first_name": "Ananya", "initial": "T", "can": ["Propose times"]})
    again = browser().get(f"/p/card/{link['code']}").json()
    assert again["acts_for"] == "Ananya T." and again["can"] == ["Propose times"]  # edits apply to cards already out
    assert "Jordan" not in link["url"] and "Ananya" not in link["url"]  # no personal data in the address


def test_an_old_long_token_still_opens_until_it_expires(owner, browser, public):
    from superaide.identity.principal.card import issue

    with use_instance(_scope(owner)), session_scope() as s:
        old = issue(s)
        past = issue(s, now=1_000_000)
    assert old and len(old) > 200
    assert browser().get(f"/p/card/{old}").status_code == 200
    assert browser().get(f"/p/card/{past}").status_code == 404


def test_revoking_all_cards_kills_short_and_long_links(owner, browser, public):
    link = new_link(owner)
    assert browser().get(f"/p/card/{link['code']}").status_code == 200
    assert owner.post("/app/principal-card/revoke", json={}).status_code == 200
    r = browser().get(f"/p/card/{link['code']}")
    assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"]
    assert browser().get(f"/p/card/{new_link(owner)['code']}").status_code == 200  # a new one works


def test_revoking_one_card_kills_only_that_link(owner, browser, public):
    a, b = new_link(owner), new_link(owner)
    with use_instance(_scope(owner)), session_scope() as s:
        card.record_sent(s, a["id"], channel="gmail", to="x@example.test")
    assert owner.post(f"/app/principal-card/sent/{a['id']}/revoke", json={}).status_code == 200
    assert browser().get(f"/p/card/{a['code']}").status_code == 404
    assert browser().get(f"/p/card/{b['code']}").status_code == 200


def test_an_expired_short_link_is_404(owner, browser, public):
    link = new_link(owner)
    with session_scope() as s:
        s.scalars(select(CardLink).where(CardLink.code == link["code"])).one().expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert browser().get(f"/p/card/{link['code']}").status_code == 404


@pytest.mark.parametrize(
    "bad", ["abcdefgh", "A" * 8, "zzzzzzzz1", "a.b", "../../x", "ab", "x" * 40, "%20%20%20%20%20%20"]
)
def test_unknown_or_junk_codes_are_the_same_404(owner, browser, public, bad):
    r = browser().get(f"/p/card/{bad}")
    assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"]


def test_opening_counts_once_per_card(owner, browser, public):
    link = new_link(owner)
    for _ in range(3):
        browser().get(f"/p/card/{link['code']}")
    assert owner.get("/app/principal-card/stats").json()["opened"] == 1


def test_the_flag_off_kills_short_links_too(owner, browser, public, monkeypatch):
    link = new_link(owner)
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    assert browser().get(f"/p/card/{link['code']}").status_code == 404


def test_the_settings_page_shows_the_outward_address(owner, public, monkeypatch):
    r = owner.get("/app/principal-card").json()
    assert r["address"] == "https://example.test" and r["address_is_local"] is False
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    r = owner.get("/app/principal-card").json()
    assert r["address"] == "http://localhost:3000" and r["address_is_local"] is True


def test_localhost_warns_only_when_the_deployment_is_reachable(caplog, monkeypatch):
    s = get_settings().model_copy(update={"public_base_url": "", "frontend_origin": "http://localhost:3000"})
    assert links.warn_if_local(s) is False  # a development machine: nothing to say
    hosted = s.model_copy(update={"session_cookie_secure": True})
    with caplog.at_level("WARNING"):
        assert links.warn_if_local(hosted) is True
    assert "PUBLIC_BASE_URL" in caplog.text
    fine = s.model_copy(update={"public_base_url": "https://example.test", "session_cookie_secure": True})
    assert links.warn_if_local(fine) is False


def test_is_local():
    assert all(links.is_local(u) for u in ("http://localhost:3000", "http://127.0.0.1", "http://box.local", ""))
    assert not links.is_local("https://example.test")


# Every link that leaves the app is built from links.public_url / public_base. A new use of `frontend_origin` in
# code that builds a link would bring the localhost bug back, so only these files may read it, each for a reason.
ALLOWED_FRONTEND_ORIGIN = {
    "platform/config.py": "the setting itself",
    "links.py": "the helper, and the allow-list of origins a request may use",
    "main.py": "CORS origins and the status page",
    "app/connections.py": "OAuth redirect URIs, which must match what is registered with the provider",
    "identity/auth/google.py": "Google sign-in redirects and its allow-list",
    "identity/auth/routes.py": "where the browser goes back to after sign-in (a redirect, not a message)",
    "platform/security/deploy.py": "decides whether the deployment looks public",
}


def test_no_link_building_code_reads_frontend_origin():
    root = Path(__file__).resolve().parents[4] / "src" / "superaide"
    users = {str(p.relative_to(root)) for p in root.rglob("*.py") if "frontend_origin" in p.read_text()}
    unexpected = users - set(ALLOWED_FRONTEND_ORIGIN)
    assert not unexpected, (
        f"build outward links with superaide.links.public_url, not frontend_origin: {sorted(unexpected)}"
    )


def test_guessing_codes_is_slowed_down_per_address_then_429(owner, browser, public, monkeypatch):
    monkeypatch.setattr(get_settings(), "card_misses_per_minute", 5)
    guesser = browser(ip="10.0.0.9")
    for i in range(5):
        r = guesser.get(f"/p/card/Zz{i}aaaaa")
        assert r.status_code == 404 and r.json()["detail"] == NOT_FOUND["detail"]  # a miss looks like every other miss
    r = guesser.get("/p/card/Zz9aaaaa")
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    good = new_link(owner)
    assert (
        guesser.get(f"/p/card/{good['code']}").status_code == 429
    )  # blocked until the minute is up, even for a real link
    assert browser(ip="10.0.0.10").get(f"/p/card/{good['code']}").status_code == 200  # another address is untouched


def test_valid_opens_never_count_toward_the_limit(owner, browser, public, monkeypatch):
    monkeypatch.setattr(get_settings(), "card_misses_per_minute", 3)
    link = new_link(owner)
    reader = browser(ip="10.0.0.11")
    assert all(reader.get(f"/p/card/{link['code']}").status_code == 200 for _ in range(10))


def test_the_window_is_a_minute(owner, public):
    from datetime import timedelta

    ip = "10.0.0.12"
    t0 = datetime(2026, 10, 24, 12, 0, 5, tzinfo=UTC)
    with session_scope() as s:
        for _ in range(30):
            card.record_miss(s, ip, now=t0)
        assert card.blocked(s, ip, now=t0 + timedelta(seconds=10))
        assert not card.blocked(s, ip, now=t0 + timedelta(seconds=70))  # the next minute starts clean


def test_a_limit_of_zero_means_no_limit(owner, browser, public, monkeypatch):
    monkeypatch.setattr(get_settings(), "card_misses_per_minute", 0)
    g = browser(ip="10.0.0.13")
    assert all(g.get(f"/p/card/Qq{i}aaaaa").status_code == 404 for i in range(40))


def test_the_flag_off_answers_404_before_any_counting(owner, browser, public, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    g = browser(ip="10.0.0.14")
    assert all(g.get(f"/p/card/Ww{i}aaaaa").status_code == 404 for i in range(40))
