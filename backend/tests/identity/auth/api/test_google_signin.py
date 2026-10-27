"""Sign in with Google (OpenID Connect), against a mocked Google: never the real one.

The fake Google signs ID tokens with a key made here and publishes it as a JWKS, so the
server's checks (signature, issuer, audience, expiry, nonce) run for real.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import time_machine
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import func, select

from superaide.identity.auth import google as signin
from superaide.integrations.google import signin as google_signin
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuthEvent, UserAccount, UserSession
from superaide.platform.db.session import session_scope
from tests.api_support import OWNER, PASSWORD, login, signup

CLIENT_ID = "1234-signin.apps.googleusercontent.com"
CLIENT_SECRET = "test-signin-secret-not-real"
FRONT = "http://localhost:3000"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key-1"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64(n), "e": b64(b"\x01\x00\x01")}


def make_token(claims: dict[str, Any], *, key: rsa.RSAPrivateKey = KEY, header: dict[str, Any] | None = None) -> str:
    head = b64(json.dumps(header or {"alg": "RS256", "kid": KID, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(sig)}"


class FakeGoogle:
    """The token endpoint and the JWKS. `claims` (plus the nonce from the auth URL) go in the next
    ID token; `token` overrides the whole token."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.token: str | None = None
        self.nonce = ""
        self.jwks_calls = 0
        self.token_status = 200
        self.origin = FRONT  # the address the sign-in started on; the token request must repeat it
        self.requests: list[httpx.Request] = []

    def identity(self, **claims: Any) -> None:
        now = int(time.time())
        self.claims = {
            "iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": "g-1001",
            "email": "jordan@example.com", "email_verified": True, "name": "Jordan Whitfield",
            "iat": now, "exp": now + 3600,
        } | claims  # fmt: skip

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == google_signin.JWKS_URL:
            self.jwks_calls += 1
            return httpx.Response(200, json={"keys": [jwk(KEY, KID)]})
        if url == google_signin.TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0]
            assert form["redirect_uri"] == [f"{self.origin}/api/auth/google/callback"]
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            claims = {"nonce": self.nonce} | self.claims
            return httpx.Response(200, json={"id_token": self.token or make_token(claims), "access_token": "ya29.x"})
        return httpx.Response(404)


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_signin_enabled", True)
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", CLIENT_SECRET)
    fake = FakeGoogle()
    fake.identity()
    monkeypatch.setattr(signin, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    google_signin.clear_jwks_cache()
    yield fake
    google_signin.clear_jwks_cache()


def start(client, fake: FakeGoogle, **body: Any) -> dict[str, str]:
    r = client.post("/auth/google/start", json=body)
    assert r.status_code == 200, r.text
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}
    fake.nonce = q["nonce"]
    return q


def callback(client, **params: str) -> httpx.Response:
    return client.get("/auth/google/callback", params=params, follow_redirects=False)


def sign_in(client, fake: FakeGoogle, **body: Any) -> httpx.Response:
    q = start(client, fake, **body)
    return callback(client, code="4/fake-code", state=q["state"])


def reason(r: httpx.Response) -> str:
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("?google=", 1)[1] if "?google=" in r.headers["location"] else "ok"


def account(email: str = OWNER["email"]) -> UserAccount | None:
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == email))
        if user is not None:
            s.expunge(user)
        return user


def events(kind: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(AuthEvent).where(AuthEvent.kind == kind)) or 0


# --- off by default ------------------------------------------------------------------------------


def test_everything_is_404_while_the_flag_is_off(browser):
    c = browser()
    assert get_settings().google_signin_enabled is False
    assert c.get("/auth/google").json() == {"enabled": False}
    assert c.post("/auth/google/start", json={}).status_code == 404
    assert c.get("/auth/google/callback", params={"code": "x", "state": "y"}).status_code == 404


def test_flag_on_but_no_client_says_so_without_a_500(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_signin_enabled", True)
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    c = browser()
    assert c.get("/auth/google").json() == {"enabled": False}
    assert c.post("/auth/google/start", json={}).status_code == 503


# --- the request to Google -----------------------------------------------------------------------


def test_start_asks_only_for_who_you_are_with_pkce_and_a_nonce(browser, google):
    c = browser()
    assert c.get("/auth/google").json() == {"enabled": True}
    q = start(c, google)
    assert q["scope"] == "openid email profile"
    assert q["client_id"] == CLIENT_ID and q["response_type"] == "code"
    assert q["redirect_uri"] == f"{FRONT}/api/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert q["nonce"] and q["state"] and q["nonce"] != q["state"]
    assert "access_type" not in q  # no refresh token for signing in
    assert c.cookies.get(signin.COOKIE)  # this browser's half of the sign-in


def test_start_needs_the_csrf_header(browser, google):
    assert browser(csrf=False).post("/auth/google/start", json={}).status_code == 403


# --- happy paths -------------------------------------------------------------------------------


def test_a_new_person_gets_a_verified_account_without_a_password(browser, google):
    c = browser()
    r = sign_in(c, google, next="/agents/ava/hire", timezone="Asia/Singapore")
    assert r.status_code == 303 and r.headers["location"] == f"{FRONT}/agents/ava/hire"
    me = c.get("/auth/me").json()["user"]
    assert me["email"] == "jordan@example.com" and me["name"] == "Jordan Whitfield"
    assert me["email_verified"] is True and me["has_password"] is False and me["google"] is True
    assert me["timezone"] == "Asia/Singapore"
    user = account()
    assert user is not None and user.google_sub == "g-1001" and user.password_hash == ""
    assert events("google_signup") == 1 and events("signup") == 1
    # No password works for it, not even an empty one.
    assert login(browser(), password="anything at all here").status_code == 401
    assert not c.cookies.get(signin.COOKIE)  # the one-time cookie is cleared


def test_the_session_cookie_is_httponly_and_lax(browser, google):
    r = sign_in(browser(), google)
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("superaide_session="))
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie


def test_returning_person_is_found_by_their_google_id_even_if_the_email_changed(browser, google):
    assert reason(sign_in(browser(), google)) == "ok"
    google.identity(email="new-address@example.com")
    c = browser()
    assert reason(sign_in(c, google)) == "ok"
    assert c.get("/auth/me").json()["user"]["email"] == "jordan@example.com"  # same account, keyed on sub
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(UserAccount)) == 1
    assert events("google_login_ok") == 1


def test_an_existing_verified_account_is_linked_and_keeps_its_password(browser, google):
    c = browser()
    assert signup(c).status_code == 200
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == OWNER["email"]))
        user.email_verified_at = user.created_at
    g = browser()
    assert reason(sign_in(g, google)) == "ok"
    assert g.get("/auth/me").json()["user"]["has_password"] is True
    assert account().google_sub == "g-1001"
    assert c.get("/auth/me").status_code == 200  # their other session is untouched
    assert login(browser()).status_code == 200  # and the password still works
    assert events("google_linked") == 1


def test_linking_an_unverified_account_removes_the_squatters_password_and_sessions(browser, google):
    squatter = browser()
    assert signup(squatter).status_code == 200  # registered the victim's address first; never verified it
    victim = browser()
    assert reason(sign_in(victim, google)) == "ok"
    assert squatter.get("/auth/me").status_code == 401  # signed out
    assert login(browser()).status_code == 401  # and the password they chose no longer works
    me = victim.get("/auth/me").json()["user"]
    assert me["email_verified"] is True and me["has_password"] is False


def test_an_email_in_another_case_still_links(browser, google):
    assert signup(browser()).status_code == 200
    google.identity(email="Jordan@Example.COM")
    assert reason(sign_in(browser(), google)) == "ok"
    assert account().google_sub == "g-1001"


# --- refused -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claims", "why"),
    [
        ({"aud": "someone-else.apps.googleusercontent.com"}, "bad_audience"),
        ({"azp": "someone-else.apps.googleusercontent.com"}, "bad_audience"),
        ({"iss": "https://evil.example"}, "bad_issuer"),
        ({"exp": int(time.time()) - 3600}, "expired"),
        ({"iat": int(time.time()) + 3600}, "not_yet_valid"),
        ({"nonce": "not-the-nonce-we-sent"}, "bad_nonce"),
        ({"nonce": ""}, "bad_nonce"),
    ],
)
def test_a_token_that_fails_a_check_signs_nobody_in(browser, google, claims, why):
    google.identity(**claims)
    c = browser()
    assert reason(sign_in(c, google)) == "failed"
    assert c.get("/auth/me").status_code == 401 and account() is None
    assert events("google_fail") == 1
    from superaide.platform.db.models import AccountAudit

    with session_scope() as s:  # nobody signed in, so the line is in the platform's account log
        logged = s.scalars(select(AccountAudit).where(AccountAudit.action == "google_signin_refused")).all()
        assert [row.detail.get("reason") for row in logged] == [why]


def _forged(google: FakeGoogle, **kw: Any) -> str:
    return make_token({"nonce": google.nonce} | google.claims, **kw)


@pytest.mark.parametrize(
    "forge",
    [
        lambda g: _forged(g, key=OTHER_KEY),  # signed by someone else's key
        lambda g: _forged(g, header={"alg": "none", "kid": KID}),
        lambda g: _forged(g, header={"alg": "HS256", "kid": KID}),
        lambda g: _forged(g, header={"alg": "RS256", "kid": "unknown-kid"}),
        lambda g: _forged(g, header={"alg": "RS256"}),
        lambda g: _forged(g).rsplit(".", 1)[0] + ".",  # signature stripped
        lambda g: "not.a.jwt.at-all",
    ],
)
def test_forged_tokens_are_refused(browser, google, forge):
    c = browser()
    q = start(c, google)
    google.token = forge(google)
    assert reason(callback(c, code="x", state=q["state"])) == "failed"
    assert c.get("/auth/me").status_code == 401 and account() is None


def test_an_unverified_google_email_is_refused_and_links_nothing(browser, google):
    assert signup(browser()).status_code == 200
    google.identity(email_verified=False)
    c = browser()
    assert reason(sign_in(c, google)) == "unverified"
    assert c.get("/auth/me").status_code == 401 and account().google_sub is None
    google.identity(email_verified="true")  # a string isn't Google's boolean
    assert reason(sign_in(browser(), google)) == "unverified"


def test_an_account_already_tied_to_another_google_account_is_not_taken_over(browser, google):
    assert reason(sign_in(browser(), google)) == "ok"
    google.identity(sub="g-2002")  # a different Google account claiming the same address
    c = browser()
    assert reason(sign_in(c, google)) == "failed"
    assert c.get("/auth/me").status_code == 401 and account().google_sub == "g-1001"


def test_disabled_and_locked_accounts_stay_out(browser, google):
    assert reason(sign_in(browser(), google)) == "ok"
    with session_scope() as s:
        s.scalar(select(UserAccount)).status = "disabled"
    assert reason(sign_in(browser(), google)) == "failed"
    with session_scope() as s:
        user = s.scalar(select(UserAccount))
        user.status = "active"
        user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    assert reason(sign_in(browser(), google)) == "locked"


def test_state_is_one_use_and_tied_to_the_browser_that_started(browser, google):
    c = browser()
    q = start(c, google)
    attacker = browser()  # finishing someone else's sign-in in this browser (login CSRF)
    assert reason(callback(attacker, code="x", state=q["state"])) == "expired"
    assert attacker.get("/auth/me").status_code == 401
    # The row is used up by that try, so the real browser has to start again.
    assert reason(callback(c, code="x", state=q["state"])) == "expired"
    q = start(c, google)
    assert reason(callback(c, code="x", state=q["state"])) == "ok"
    assert reason(callback(c, code="x", state=q["state"])) == "expired"  # replay


@pytest.mark.parametrize("params", [{}, {"code": "x"}, {"state": "nope"}, {"code": "x", "state": "nope"}])
def test_missing_or_made_up_state_never_signs_in_or_errors(browser, google, params):
    c = browser()
    r = callback(c, **params)
    assert r.status_code == 303 and r.headers["location"].startswith(f"{FRONT}/login?google=")
    assert c.get("/auth/me").status_code == 401


def test_cancelling_on_googles_screen_comes_back_politely(browser, google):
    c = browser()
    q = start(c, google)
    assert reason(callback(c, error="access_denied", state=q["state"])) == "cancelled"
    assert reason(callback(c, code="x", state=q["state"])) == "expired"  # cancelled uses it up


def test_google_refusing_the_code_or_being_down_is_a_plain_failure(browser, google, monkeypatch):
    google.token_status = 400
    assert reason(sign_in(browser(), google)) == "failed"
    google.token_status = 200

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(signin, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(down)))
    assert reason(sign_in(browser(), google)) == "failed"


def test_an_old_start_expires(browser, google):
    c = browser()
    q = start(c, google)
    with time_machine.travel(datetime.now(UTC) + timedelta(minutes=11)):
        assert reason(callback(c, code="x", state=q["state"])) == "expired"


@pytest.mark.parametrize("bad", ["//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:alert(1)"])
def test_next_never_leaves_the_site(browser, google, bad):
    r = sign_in(browser(), google, next=bad)
    assert r.headers["location"] == f"{FRONT}/login"


def test_the_jwks_is_cached(browser, google):
    for _ in range(3):
        assert reason(sign_in(browser(), google)) == "ok"
    assert google.jwks_calls == 1


def test_failures_are_throttled_per_device(browser, google, settings, monkeypatch):
    monkeypatch.setattr(settings, "login_failures_per_ip", 3)
    google.identity(aud="wrong.apps.googleusercontent.com")
    c = browser(ip="10.0.0.9")
    for _ in range(3):
        assert reason(sign_in(c, google)) == "failed"
    assert c.post("/auth/google/start", json={}).status_code == 429
    assert browser(ip="10.0.0.10").post("/auth/google/start", json={}).status_code == 200  # others unaffected


# --- enumeration -------------------------------------------------------------------------------


def test_new_and_existing_accounts_look_the_same_from_outside(browser, google):
    """Whether an address already had an account changes nothing visible: same redirect, same page."""
    first = sign_in(browser(), google, next="/workspace")
    google.identity(sub="g-3003", email="newcomer@example.com", name="New Comer")
    second = sign_in(browser(), google, next="/workspace")
    assert first.headers["location"] == second.headers["location"] == f"{FRONT}/workspace"
    # Starting reveals nothing about any address: there isn't one yet.
    assert browser().post("/auth/google/start", json={"next": "/x"}).json().keys() == {"url"}


def test_password_login_is_unchanged_for_everyone_else(browser, google):
    assert signup(browser()).status_code == 200
    assert login(browser(), password=PASSWORD).status_code == 200
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(UserSession)) == 2


def test_with_many_owners_a_new_google_account_can_hire_its_own_ava(browser, google, monkeypatch):
    from tests.api_support import hire

    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    first = browser()
    assert signup(first).status_code == 200 and hire(first).status_code == 200
    google.identity(sub="g-4004", email="newcomer@example.com", name="New Comer")
    c = browser()
    assert reason(sign_in(c, google, next="/agents/ava/hire")) == "ok"
    assert hire(c).status_code == 200
    me = c.get("/app/me")
    assert me.status_code == 200 and "Jordan" not in me.text
    assert first.get("/app/me").status_code == 200  # the first owner keeps theirs


# --- the address the person is using ----------------------------------------------------------------

ALT = "http://127.0.0.1:3000"


def test_signing_in_from_the_other_local_address_needs_it_registered_with_google(browser, google):
    """127.0.0.1 is ours (links.py) but Google only knows the addresses registered in its console."""
    c = browser()
    r = c.post("/auth/google/start", json={}, headers={"Origin": ALT})
    assert r.status_code == 409 and FRONT in r.json()["detail"] and "Google sign-in works only at" in r.json()["detail"]
    assert not c.cookies.get(signin.COOKIE)  # nothing started


def test_a_registered_address_signs_in_there_end_to_end(browser, google, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_redirect_origins", ALT)
    c = browser()
    r = c.post("/auth/google/start", json={"next": "/workspace"}, headers={"Origin": ALT})
    assert r.status_code == 200
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}
    assert q["redirect_uri"] == f"{ALT}/api/auth/google/callback"
    google.nonce = q["nonce"]
    google.origin = ALT  # the token request must repeat the redirect address Google was given
    done = callback(c, code="4/fake-code", state=q["state"])
    assert done.status_code == 303, done.text
    assert done.headers["location"] == f"{ALT}/workspace"  # not localhost
    assert c.get("/auth/me").status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:3000.evil.example", "javascript://x"])
def test_an_address_that_is_not_ours_signs_in_on_the_normal_one(browser, google, monkeypatch, origin):
    monkeypatch.setattr(get_settings(), "google_redirect_origins", "https://evil.example")  # even if misconfigured
    c = browser()
    r = c.post("/auth/google/start", json={}, headers={"Origin": origin})
    assert r.status_code == 200
    assert parse_qs(urlparse(r.json()["url"]).query)["redirect_uri"] == [f"{FRONT}/api/auth/google/callback"]


def test_a_stored_address_that_is_no_longer_registered_is_never_redirected_to(browser, google, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_redirect_origins", ALT)
    c = browser()
    q = parse_qs(urlparse(c.post("/auth/google/start", json={}, headers={"Origin": ALT}).json()["url"]).query)
    google.nonce = q["nonce"][0]
    monkeypatch.setattr(get_settings(), "google_redirect_origins", "")  # un-registered meanwhile
    r = callback(c, code="x", state=q["state"][0])
    assert r.headers["location"].startswith(f"{FRONT}/") and ALT not in r.headers["location"]


# --- coming back after a failure -----------------------------------------------------------------


def test_cancelling_returns_to_the_page_it_started_on_with_next_kept(browser, google):
    c = browser()
    q = start(c, google, next="/agents/ava/hire", page="signup")
    r = callback(c, error="access_denied", state=q["state"])
    assert r.headers["location"] == f"{FRONT}/signup?google=cancelled&next=%2Fagents%2Fava%2Fhire"


def test_a_failure_from_google_keeps_next_and_defaults_to_the_login_page(browser, google):
    google.token_status = 400
    c = browser()
    q = start(c, google, next="/workspace?tab=a&b=c", page="elsewhere")
    r = callback(c, code="x", state=q["state"])
    assert r.headers["location"] == f"{FRONT}/login?google=failed&next=%2Fworkspace%3Ftab%3Da%26b%3Dc"


def test_a_next_that_is_another_site_is_not_carried_back(browser, google):
    c = browser()
    q = start(c, google, next="//evil.example/x", page="signup")
    r = callback(c, error="access_denied", state=q["state"])
    assert r.headers["location"] == f"{FRONT}/signup?google=cancelled"


# --- an account with no password ---------------------------------------------------------------------


def test_a_google_only_owner_is_told_how_to_set_a_password_not_that_theirs_is_wrong(browser, google):
    c = browser()
    assert reason(sign_in(c, google)) == "ok"
    for _ in range(get_settings().login_max_failures + 2):  # never counted toward the lockout
        r = c.post("/auth/email", json={"email": "new@example.com", "password": "anything I could think of"})
        assert r.status_code == 400 and "Google" in r.json()["detail"] and "Forgot your password" in r.json()["detail"]
        assert "isn't right" not in r.text
    r = c.post("/auth/password", json={"current": "whatever it was", "new": "a long enough new passphrase"})
    assert r.status_code == 400 and "Google" in r.json()["detail"]
    assert account() is not None and account("new@example.com") is None
    assert events("email_change_fail") == 0 and events("password_change_fail") == 0
