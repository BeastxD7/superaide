"""Accounts over HTTP: what sign-up, login, sessions and password changes refuse, and how."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from superaide.auth import service
from superaide.config import get_settings
from superaide.db.models import AuthEvent, Person, UserAccount, UserSession
from superaide.db.session import session_scope
from tests.api_support import OTHER, OWNER, PASSWORD, app_routes, fill_path, login, signup

GENERIC_LOGIN_ERROR = "That email and password don't match."


def _count(model, *where) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


@pytest.fixture
def clock(monkeypatch):
    """Freeze the auth service's clock; tests move it by assigning clock[0]."""
    now = [datetime.now(UTC)]
    monkeypatch.setattr(service, "_now", lambda: now[0])
    return now


# --- sign-up ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short1!", "at least 10"),
        ("x" * 9, "at least 10"),
        pytest.param("Ab1!" * 65, "at most 256", id="260-chars"),
        ("password123", "too easy"),
        ("PASSWORD123", "too easy"),  # case doesn't make a common password safe
        ("qwertyuiop", "too easy"),
        ("aaaaaaaaaaaaaaaa", "too easy"),  # fewer than 4 different characters
        ("abababababababab", "too easy"),
        ("JordanWhitfield is great", "name or email"),  # the name, spaces removed, case ignored
        ("i am jordan 2026", "name or email"),  # the email's local part
    ],
)
def test_signup_refuses_weak_passwords_with_a_reason(browser, password, message):
    r = signup(browser(), password=password)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["field"] == "password" and message in r.json()["detail"]["message"]
    assert _count(UserAccount) == 0 and _count(Person) == 0  # nothing half-made


def test_a_password_of_exactly_ten_or_256_characters_is_fine(browser):
    assert signup(browser(), password="t" * 3 + "w1!" + "q" * 4).status_code == 200
    assert signup(browser(), email="b@example.com", password=("Zq9!" * 64)).status_code == 200


@pytest.mark.parametrize(
    "email",
    ["not-an-email", "a@b", "@example.com", "jordan@", "a@@example.com", "a b@example.com", "a@exa mple.com", "  "],
)
def test_signup_refuses_invalid_emails(browser, email):
    r = signup(browser(), email=email)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # Either our own message (field "email") or the request validator's (email too short).
    assert (isinstance(detail, dict) and detail["field"] == "email") or detail[0]["loc"][-1] == "email"
    assert _count(UserAccount) == 0


@pytest.mark.parametrize(
    "variant", ["JORDAN@EXAMPLE.COM", "  jordan@example.com  ", "Jordan@Example.com", "\tjordan@example.com\n"]
)
def test_duplicate_email_in_any_case_or_spacing_is_refused(browser, variant):
    assert signup(browser()).status_code == 200
    again = signup(browser(), email=variant, name="Someone Else", password="another long passphrase")
    assert again.status_code == 409 and again.json()["detail"]["field"] == "email"
    assert _count(UserAccount) == 1 and _count(Person) == 0  # a refused sign-up leaves no person row (none until hire)
    # The original account still works with its own password, and only with it.
    assert login(browser()).status_code == 200
    assert login(browser(), password="another long passphrase").status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("name", "n" * 121, id="name-121"),
        pytest.param("email", "e" * 310 + "@example.com", id="email-322"),
        pytest.param("password", "p" * 1025, id="password-1025"),
        ("name", ""),
        ("password", ""),
        ("name", 12345),
        ("email", ["jordan@example.com"]),
        ("password", None),
    ],
)
def test_signup_refuses_overlong_empty_or_wrongly_typed_fields(browser, field, value):
    r = signup(browser(), **{field: value})
    assert r.status_code == 422, r.text
    assert _count(UserAccount) == 0


def test_signup_needs_every_field(browser):
    for missing in ("name", "email", "password"):
        body = {k: v for k, v in OWNER.items() if k != missing}
        assert browser().post("/auth/signup", json=body).status_code == 422
    assert (
        browser().post("/auth/signup", content=b"{not json", headers={"Content-Type": "application/json"}).status_code
        == 422
    )


def test_signup_name_of_only_spaces_is_refused(browser):
    r = signup(browser(), name="   \t ")
    assert r.status_code == 422 and r.json()["detail"]["field"] == "name"


def test_signup_name_is_tidied(browser):
    r = signup(browser(), name="  Jordan \n  Whitfield  ")
    assert r.status_code == 200 and r.json()["user"]["name"] == "Jordan Whitfield"


@pytest.mark.parametrize("name", ["Jordan\x00Rao", "\x00", "Back\\\x00slash"])
def test_signup_with_a_nul_character_is_refused_not_a_server_error(browser, name):
    """Postgres can't store NUL; this used to fail on save with a 500."""
    r = signup(browser(), name=name)
    assert r.status_code == 422, r.text
    assert "NUL" in r.json()["detail"]
    assert _count(UserAccount) == 0 and _count(Person) == 0


def test_text_that_merely_looks_like_a_nul_escape_is_fine(browser):
    r = signup(browser(), name="Back\\u0000slash")  # a backslash, then the letters u0000
    assert r.status_code == 200 and r.json()["user"]["name"] == "Back\\u0000slash"


def test_signups_from_one_address_are_throttled(browser, settings, monkeypatch):
    monkeypatch.setattr(settings, "signups_per_ip_per_hour", 2)
    for i in range(2):
        assert signup(browser(), email=f"user{i}@example.com").status_code == 200
    r = signup(browser(), email="third@example.com")
    assert r.status_code == 429 and "Too many new accounts" in r.json()["detail"]
    # Another address is not affected.
    assert signup(browser(ip="10.9.9.9"), email="fourth@example.com").status_code == 200
    # Pretending to be another address with a header doesn't help.
    spoof = browser().post(
        "/auth/signup",
        json={**OWNER, "email": "fifth@example.com"},
        headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "1.2.3.4"},
    )
    assert spoof.status_code == 429


# --- login -------------------------------------------------------------------------


def test_unknown_email_and_wrong_password_look_the_same(browser):
    signup(browser())
    answers = [
        login(browser(), password="not the password at all"),
        login(browser(), email="nobody@example.com"),
        login(browser(), email="not even an email"),
        login(browser(), email="JORDAN@example.com", password="Correct Horse Battery"),  # case matters
    ]
    assert {r.status_code for r in answers} == {401}
    assert {r.json()["detail"] for r in answers} == {GENERIC_LOGIN_ERROR}
    assert all("set-cookie" not in r.headers for r in answers)


def test_login_works_with_the_email_in_any_case_or_spacing(browser):
    signup(browser())
    assert login(browser(), email="  JORDAN@Example.COM ").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "jordan@example.com"},
        {"password": PASSWORD},
        {"email": "", "password": PASSWORD},
        {"email": "jordan@example.com", "password": ""},
        {"email": 1, "password": 2},
        pytest.param({"email": "e" * 321, "password": PASSWORD}, id="email-321"),
        pytest.param({"email": "jordan@example.com", "password": "p" * 1025}, id="password-1025"),
    ],
)
def test_malformed_login_requests_are_refused(browser, body):
    signup(browser())
    r = browser().post("/auth/login", json=body)
    assert r.status_code == 422, r.text
    assert _count(AuthEvent, AuthEvent.kind == "login_ok") == 0


def test_login_with_a_nul_character_is_refused_not_a_server_error(browser):
    signup(browser())
    for body in ({"email": "jordan@example.com\x00", "password": PASSWORD}, {"email": "\x00", "password": "x"}):
        r = browser().post("/auth/login", json=body)
        assert r.status_code == 422, r.text
    assert login(browser()).status_code == 200


def test_account_locks_after_five_failures_and_unlocks_after_the_window(browser, settings, clock):
    signup(browser())
    attacker = browser()
    for _ in range(settings.login_max_failures - 1):
        assert login(attacker, password="wrong password guess").status_code == 401
    # Four failures are not a lock: the owner can still get in, and that resets the count.
    assert login(browser()).status_code == 200
    for _ in range(settings.login_max_failures - 1):
        assert login(attacker, password="wrong password guess").status_code == 401
    assert login(attacker, password="wrong password guess").status_code == 401  # the fifth in a row
    locked = login(browser())  # even the right password now
    assert locked.status_code == 429 and "Try again in" in locked.json()["detail"]
    assert "set-cookie" not in locked.headers

    clock[0] += timedelta(minutes=settings.login_lock_minutes - 1)
    assert login(browser()).status_code == 429  # still locked a minute before the end
    clock[0] += timedelta(minutes=1, seconds=1)
    assert login(browser()).status_code == 200  # unlocked
    # The lock cleared the counter: one more mistake is not a new lock.
    assert login(attacker, password="wrong password guess").status_code == 401
    assert login(browser()).status_code == 200


def test_failed_attempts_from_one_address_are_throttled(browser, settings, monkeypatch):
    monkeypatch.setattr(settings, "login_failures_per_ip", 3)
    signup(browser())
    attacker = browser(ip="203.0.113.7")
    for i in range(3):
        assert login(attacker, email=f"guess{i}@example.com").status_code == 401
    # Now this address is refused, even with the right password, and headers don't change who it is.
    assert login(attacker).status_code == 429
    spoof = attacker.post(
        "/auth/login", json={"email": OWNER["email"], "password": PASSWORD}, headers={"X-Forwarded-For": "8.8.8.8"}
    )
    assert spoof.status_code == 429
    # Someone elsewhere is unaffected.
    assert login(browser(ip="198.51.100.1")).status_code == 200


def test_disabled_accounts_cannot_log_in_or_use_old_sessions(browser):
    c = browser()
    signup(c)
    with session_scope() as s:
        s.execute(update(UserAccount).values(status="disabled"))
    assert c.get("/auth/me").status_code == 401
    r = login(browser())
    assert r.status_code == 401 and r.json()["detail"] == GENERIC_LOGIN_ERROR


# --- sessions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cookie",
    ["forged-token", "", pytest.param("x" * 10_000, id="10k"), "' OR 1=1 --", "%00%ff", "ey.J9.Zm9v"],
)
def test_forged_or_garbage_session_cookies_are_refused(browser, cookie):
    signup(browser())
    c = browser()
    c.cookies.set("superaide_session", cookie)
    for path in ("/auth/me", "/app/me", "/app/notebook", "/app/passages/6f1f5b8e-0000-4000-8000-000000000009"):
        r = c.get(path)
        assert r.status_code == 401, (path, r.text)
        assert r.json()["detail"] == "Please log in."


def test_another_users_token_hash_is_not_a_session(browser):
    signup(browser())
    with session_scope() as s:
        stored_hash = s.scalar(select(UserSession.token_hash))
    c = browser()
    c.cookies.set("superaide_session", stored_hash)  # what a database leak would reveal
    assert c.get("/auth/me").status_code == 401


def test_expired_and_revoked_sessions_are_refused(browser, settings, clock):
    c = browser()
    signup(c)
    clock[0] += timedelta(days=settings.session_days, seconds=1)
    assert c.get("/auth/me").status_code == 401
    d = browser()
    clock[0] = datetime.now(UTC)
    login(d)
    with session_scope() as s:
        s.execute(update(UserSession).values(revoked_at=datetime.now(UTC)))
    assert d.get("/auth/me").status_code == 401


def test_sessions_slide_while_used(browser, settings, clock):
    c = browser()
    signup(c)
    for _ in range(3):  # three uses, each just inside the window, keep the session alive
        clock[0] += timedelta(days=settings.session_days - 1)
        assert c.get("/auth/me").status_code == 200


def test_logout_revokes_on_the_server_and_only_this_device(browser):
    phone, laptop = browser(), browser()
    signup(phone)
    login(laptop)
    token = phone.cookies.get("superaide_session")
    r = phone.post("/auth/logout")
    assert r.status_code == 200 and 'superaide_session=""' in r.headers["set-cookie"]
    replay = browser()
    replay.cookies.set("superaide_session", token)
    assert replay.get("/auth/me").status_code == 401
    assert laptop.get("/auth/me").status_code == 200  # the other device is still in
    # Logging out again, or without any session, is harmless.
    assert replay.post("/auth/logout").status_code == 200
    assert browser().post("/auth/logout").status_code == 200
    garbage = browser()
    garbage.cookies.set("superaide_session", "nonsense")
    assert garbage.post("/auth/logout").status_code == 200
    assert _count(AuthEvent, AuthEvent.kind == "logout") == 1


def test_logout_everywhere_revokes_every_session(browser):
    devices = [browser() for _ in range(3)]
    signup(devices[0])
    for d in devices[1:]:
        login(d)
    signup(other := browser(), **OTHER)
    r = devices[0].post("/auth/logout-everywhere")
    assert r.status_code == 200 and r.json()["sessions"] == 3
    assert all(d.get("/auth/me").status_code == 401 for d in devices)
    assert other.get("/auth/me").status_code == 200  # someone else's account is untouched
    assert browser().post("/auth/logout-everywhere").status_code == 401
    assert devices[0].post("/auth/logout-everywhere").status_code == 401


# --- changing the password ---------------------------------------------------------------


def test_changing_password_needs_a_session(browser):
    signup(browser())
    r = browser().post("/auth/password", json={"current": PASSWORD, "new": "a brand new passphrase"})
    assert r.status_code == 401


def test_wrong_current_password_changes_nothing(browser):
    me, laptop = browser(), browser()
    signup(me)
    login(laptop)
    r = me.post("/auth/password", json={"current": "not my password", "new": "a brand new passphrase"})
    assert r.status_code == 401 and r.json()["detail"]["field"] == "current"
    assert laptop.get("/auth/me").status_code == 200  # nobody was signed out
    assert login(browser()).status_code == 200  # the old password still works
    assert login(browser(), password="a brand new passphrase").status_code == 401


@pytest.mark.parametrize(
    ("new", "message"), [("short", "at least 10"), ("password123", "too easy"), ("jordan whitfield 1", "name or email")]
)
def test_weak_new_password_is_refused_and_nothing_changes(browser, new, message):
    me, laptop = browser(), browser()
    signup(me)
    login(laptop)
    r = me.post("/auth/password", json={"current": PASSWORD, "new": new})
    assert r.status_code == 422 and message in r.json()["detail"]["message"]
    assert laptop.get("/auth/me").status_code == 200
    assert login(browser()).status_code == 200


def test_new_password_signs_out_old_sessions_and_the_old_password(browser):
    me, laptop, tablet = browser(), browser(), browser()
    signup(me)
    login(laptop)
    login(tablet)
    old_laptop_cookie = laptop.cookies.get("superaide_session")
    r = me.post("/auth/password", json={"current": PASSWORD, "new": "a brand new passphrase"})
    assert r.status_code == 200
    assert me.get("/auth/me").status_code == 200
    assert laptop.get("/auth/me").status_code == 401 and tablet.get("/auth/me").status_code == 401
    replay = browser()
    replay.cookies.set("superaide_session", old_laptop_cookie)
    assert replay.get("/auth/me").status_code == 401
    assert login(browser()).status_code == 401
    assert login(browser(), password="a brand new passphrase").status_code == 200


def test_guessing_the_current_password_is_limited(browser, settings, clock):
    """A stolen session must not become a way to brute-force the account's password."""
    me = browser()
    signup(me)
    for i in range(settings.login_max_failures):
        r = me.post("/auth/password", json={"current": f"guess number {i}", "new": "a brand new passphrase"})
        assert r.status_code == 401
    r = me.post("/auth/password", json={"current": PASSWORD, "new": "a brand new passphrase"})
    assert r.status_code == 429, r.text
    assert login(browser(), password="a brand new passphrase").status_code == 401  # not changed
    # After the lock window the owner can change it again. (The failures were recorded at real
    # time, a few seconds after the frozen clock started, hence the extra minute.)
    clock[0] += timedelta(minutes=settings.login_lock_minutes + 1)
    r = me.post("/auth/password", json={"current": PASSWORD, "new": "a brand new passphrase"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "body", [{}, {"current": PASSWORD}, {"new": "a brand new passphrase"}, {"current": 1, "new": 2}]
)
def test_malformed_password_change_requests_are_refused(browser, body):
    me = browser()
    signup(me)
    assert me.post("/auth/password", json=body).status_code == 422


# --- CSRF and CORS -------------------------------------------------------------------


def test_every_unsafe_app_and_auth_route_needs_the_csrf_header(app, browser, owner):
    """Enumerated from the router, so a new endpoint can't forget the check."""
    routes = [(m, p) for m, p in app_routes(app, "/auth") + app_routes(app, "/app") if m != "GET"]
    assert len(routes) >= 15  # sanity: the enumeration found the API
    cookie = owner.cookies.get("superaide_session")
    for value in (None, ""):
        plain = browser(csrf=False)
        plain.cookies.set("superaide_session", cookie)  # signed in as the owner, like a forged cross-site form
        headers = {} if value is None else {"X-Requested-With": value}
        for method, path in routes:
            r = plain.request(method, fill_path(path), json={}, headers=headers)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            assert r.json()["detail"] == "Missing X-Requested-With header"
    # Nothing happened as the owner: still signed in, still the owner.
    assert owner.get("/app/me").status_code == 200


def test_the_voice_calls_audio_upload_needs_the_csrf_header_and_a_session(browser, owner, settings, monkeypatch):
    """A multipart form is what a cross-site page can send without a preflight, so the voice call's
    utterance upload (ADR 0012) must be refused without X-Requested-With, and without a session."""
    monkeypatch.setattr(settings, "voice_intro_v2", True)
    form = {"files": {"audio": ("a.wav", b"RIFF0000WAVE", "audio/wav")}, "data": {"text": "hi"}}
    plain = browser(csrf=False)
    plain.cookies.set("superaide_session", owner.cookies.get("superaide_session"))
    r = plain.post("/app/intro/voice/turn", **form)
    assert r.status_code == 403 and r.json()["detail"] == "Missing X-Requested-With header"
    for path in ("/app/intro/voice/turn", "/app/intro/voice/start", "/app/intro/voice/confirm"):
        assert browser().post(path, **form).status_code == 401, path


def test_reads_do_not_need_the_csrf_header(browser, owner):
    plain = browser(csrf=False)
    plain.cookies.set("superaide_session", owner.cookies.get("superaide_session"))
    assert plain.get("/auth/me").status_code == 200 and plain.get("/app/me").status_code == 200


def test_the_gmail_routes_need_the_csrf_header_and_refuse_bad_input(browser, owner):
    plain = browser(csrf=False)
    plain.cookies.set("superaide_session", owner.cookies.get("superaide_session"))
    for path in ("/app/gmail/connect", "/app/gmail/drafts", "/app/gmail/addresses"):
        assert plain.post(path, json={}).status_code == 403
    assert owner.post("/app/gmail/drafts", json={"enabled": "maybe"}).status_code == 422
    assert owner.post("/app/gmail/drafts", json={}).status_code == 422
    for body in ({"person_id": "x", "email": "a@b.co"}, {"person_id": str(uuid.uuid4()), "email": "a" * 300},
                 {"person_id": str(uuid.uuid4()), "email": "not an email"}, {"email": "a@b.co"}):  # fmt: skip
        assert owner.post("/app/gmail/addresses", json=body).status_code == 422, body
    assert owner.post("/app/gmail/addresses", json={"person_id": str(uuid.uuid4()), "email": "a\u0000@b.co"}
                      ).status_code in (400, 422)  # fmt: skip


ALLOWED = "http://localhost:3000"


@pytest.mark.parametrize("path", ["/auth/login", "/auth/signup", "/app/hire", "/app/providers/anthropic"])
def test_preflight_from_the_frontend_is_allowed(browser, path):
    r = browser().options(
        path,
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-requested-with",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert r.headers["access-control-allow-credentials"] == "true"
    assert "x-requested-with" in r.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "null",
        "http://localhost:3000.evil.example",
        "http://localhost:3001",
        "https://localhost:3000",
        "http://evil.localhost:3000",
    ],
)
def test_preflight_from_another_origin_gets_no_allow_origin(browser, origin):
    r = browser().options(
        "/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-requested-with",
        },
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    # A plain request from that origin is answered without CORS headers, so the page can't read it.
    signup(browser())
    c = browser()
    login(c)
    r = c.get("/auth/me", headers={"Origin": origin})
    assert "access-control-allow-origin" not in r.headers


def test_preflight_for_methods_or_headers_we_dont_use_is_refused(browser):
    for method, headers in (("DELETE", "x-requested-with"), ("PUT", "x-requested-with"), ("POST", "x-custom")):
        r = browser().options(
            "/auth/logout",
            headers={
                "Origin": ALLOWED,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": headers,
            },
        )
        assert r.status_code == 400, (method, headers)


SECRET_PASSWORD = "my-very-secret-passphrase"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/auth/signup", {"name": "Jordan Whitfield", "email": "s@example.com", "password": SECRET_PASSWORD * 50}),
        ("/auth/signup", {"name": "", "email": "s@example.com", "password": SECRET_PASSWORD}),
        ("/auth/login", {"password": SECRET_PASSWORD}),  # a missing field's "input" is the whole body
        ("/auth/login", {"email": ["s@example.com"], "password": SECRET_PASSWORD}),
        ("/auth/password", {"current": SECRET_PASSWORD}),
    ],
    ids=["signup-too-long", "signup-no-name", "login-no-email", "login-bad-email", "change-no-new"],
)
def test_validation_errors_never_echo_a_password(browser, path, body):
    c = browser()
    signup(c, email="other@example.com")
    r = c.post(path, json=body)
    assert r.status_code == 422
    assert SECRET_PASSWORD not in r.text
    first = r.json()["detail"][0]
    assert first["loc"] and first["msg"]  # still says what's wrong, for the form


# --- Sign in with Google (the flows are in test_google_signin.py) -----------------------------------


@pytest.fixture
def google_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_signin_enabled", True)
    monkeypatch.setattr(settings, "google_client_id", "1234-signin.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "test-signin-secret-not-real")


def test_google_sign_in_routes_ask_for_no_session_and_reveal_nothing(browser, owner, google_on):
    """GET /auth/google, POST /auth/google/start and GET /auth/google/callback: public by design
    (they sign people in), so the same answers whoever asks, and never an account's data."""
    for c in (browser(), owner):
        assert c.get("/auth/google").json() == {"enabled": True}
        r = c.post("/auth/google/start", json={"next": "/workspace"})
        assert r.status_code == 200 and r.json().keys() == {"url"}
        assert OWNER["email"] not in r.text and "Jordan" not in r.text
        r = c.get("/auth/google/callback", params={"code": "x", "state": "nope"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/login?google=expired")
    assert owner.get("/auth/me").status_code == 200  # a failed callback doesn't sign the owner out


def test_google_start_needs_the_csrf_header_and_valid_fields(browser, google_on):
    assert browser(csrf=False).post("/auth/google/start", json={}).status_code == 403
    for body in ({"next": "x" * 301}, {"timezone": "x" * 65}, {"next": ["/a"]}):
        assert browser().post("/auth/google/start", json=body).status_code == 422, body


def test_google_preflight_from_another_origin_gets_no_allow_origin(browser, google_on):
    r = browser().options(
        "/auth/google/start",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type,x-requested-with"},
    )  # fmt: skip
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers


def test_switching_calendar_actions_needs_the_csrf_header(browser, owner):
    plain = browser(csrf=False)
    plain.cookies.set("superaide_session", owner.cookies.get("superaide_session"))
    body = {"setting": "actions", "on": True, "accepted": ["write_external", "send"]}
    r = plain.post("/app/skills/superaide/google-calendar/options", json=body)
    assert r.status_code == 403 and r.json()["detail"] == "Missing X-Requested-With header"
