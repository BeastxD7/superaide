"""Who may call what: every owner endpoint under /app, enumerated from the app's route table.

Signed out → 401, signed in but not the owner → 403, and neither answer carries
anything the owner shared. Then the ID-based endpoints, which must not act on
rows that belong to someone else.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Document, MemoryItem, Person, UserAccount
from superaide.db.session import session_scope
from superaide.knowledge.store import remember_fact
from tests.api.conftest import OWNER, app_routes, fill_path, signup

PUBLIC = {("GET", "/app/agents")}  # the marketplace
ACCOUNT_ONLY = {("POST", "/app/hire")}  # any signed-in account (tested in test_hire_negative)
# Owner routes behind a setting that's off by default: the owner gets 404 until it's on (ADR 0010
# triage, TRIAGE_ENABLED). Everyone else is refused exactly as on any owner route.
FLAG_GATED = {
    ("GET", "/app/needs-you"),
    ("GET", "/app/triage/settings"),
    ("GET", "/app/approvals/brake"),  # RUBBER_STAMP_BRAKE_ENABLED (#134)
    # The "Ask my Ava" link (ASK_LINK_ENABLED); its public side is checked in test_public_ask_routes below.
    ("GET", "/app/ask-link"),
    ("GET", "/app/ask-link/requests"),
    # The introduction's voice call, v2 (VOICE_INTRO_V2, ADR 0012); its POSTs are enumerated like every route.
    ("GET", "/app/intro/voice"),
}
SECRET_NOTE = "Project Bluebird is my secret side project"


@pytest.fixture(autouse=True)
def push_on(monkeypatch):
    """Web push (ADR 0011) answers 404 while off; switched on here so its routes are checked too."""
    from superaide.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"a" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    monkeypatch.setattr(get_settings(), "push_enabled", True)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


@pytest.fixture
def owner_data(owner) -> dict[str, str]:
    """The owner has said and shared things; returns strings that must never reach anyone else."""
    owner.post("/app/onboarding", json={"step": "hello", "answer": {"choice": "Nice to meet you"}})
    owner.post("/app/onboarding", json={"step": "about", "answer": {"mode": "chat", "text": SECRET_NOTE}})
    doc = owner.post(
        "/app/intro/files", files={"file": ("bluebird-plan.txt", b"Bluebird launch plan, Q3", "text/plain")}
    )
    assert doc.status_code == 200, doc.text
    with session_scope() as s:
        account = s.scalar(select(UserAccount).where(UserAccount.email_normalized == OWNER["email"]))
        memory = s.scalar(select(MemoryItem.id).where(MemoryItem.content.contains("Bluebird")))
        return {
            "email": OWNER["email"],
            "surname": "Rao",
            "user_id": str(account.id),
            "person_id": str(account.person_id),
            "note": "Bluebird",
            "document_id": doc.json()["document_id"],
            "memory_id": str(memory),
        }


def owner_routes(app) -> list[tuple[str, str]]:
    routes = [r for r in app_routes(app, "/app") if r not in PUBLIC | ACCOUNT_ONLY]
    assert len(routes) >= 15, routes  # the enumeration found the owner API
    return routes


def _call(client, method: str, path: str, ids: dict[str, str] | None = None):
    url = fill_path(path)
    if ids:  # aim at the owner's real rows, which is what an attacker would try
        url = path.replace("{document_id}", ids["document_id"]).replace("{memory_id}", ids["memory_id"])
        url = fill_path(url)
    if path == "/app/intro/files":
        return client.request(method, url, files={"file": ("x.txt", b"hello", "text/plain")})
    return client.request(method, url, json={} if method != "GET" else None)


def _assert_no_owner_data(r, owner_data: dict[str, str]) -> None:
    for name, value in owner_data.items():
        if name not in {"document_id", "memory_id"}:  # those were in the request URL; the body must not echo more
            assert value not in r.text, (name, r.request.method, r.request.url, r.text)


def test_signed_out_gets_401_everywhere(app, browser, owner_data):
    anonymous = browser()
    for method, path in owner_routes(app):
        r = _call(anonymous, method, path, owner_data)
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        assert r.json() == {"detail": "Please log in."}


def test_a_signed_in_stranger_gets_403_everywhere_and_sees_nothing(app, browser, owner_data):
    stranger = browser()
    assert signup(stranger, name="Mallory Tan", email="mallory@example.com").status_code == 200
    for method, path in owner_routes(app):
        r = _call(stranger, method, path, owner_data)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        _assert_no_owner_data(r, owner_data)
    # Nothing they tried changed the owner's data.
    with session_scope() as s:
        assert s.get(Document, uuid.UUID(owner_data["document_id"])).removed_at is None
        assert s.get(MemoryItem, uuid.UUID(owner_data["memory_id"])).valid_to is None
    assert stranger.get("/auth/me").json()["hire"] is None


def test_before_anyone_hires_every_owner_endpoint_is_403(app, browser):
    c = browser()
    signup(c)
    for method, path in owner_routes(app):
        r = _call(c, method, path)
        assert r.status_code == 403, (method, path, r.status_code, r.text)


def test_the_owner_can_read_every_owner_page(app, owner_data, owner):
    for method, path in owner_routes(app):
        if method == "GET" and not path.endswith("/callback"):  # OAuth callbacks are redirects, not pages
            expected = 404 if (method, path) in FLAG_GATED else 200
            assert owner.get(fill_path(path)).status_code == expected, path


def test_the_admin_token_is_not_an_app_session(app, browser, owner_data):
    c = browser()
    for method, path in owner_routes(app):
        r = c.request(method, fill_path(path), headers={"Authorization": "Bearer test-admin-token"})
        assert r.status_code == 401, (method, path)


def test_an_app_session_is_not_the_admin_token(owner):
    for path in ("/api/overview", "/api/people", "/api/memories", "/api/events"):
        assert owner.get(path).status_code == 401


# --- endpoints that take an ID -------------------------------------------------------------


def _other_persons_rows() -> tuple[str, str]:
    """A document and a fact that belong to someone other than the owner (e.g. a contact)."""
    with session_scope() as s:
        someone = Person(display_name="Priya Tan", relation="contact")
        s.add(someone)
        s.flush()
        doc = Document(owner_person_id=someone.id, kind="text", title="priya.txt", source="priya.txt", sha256="0" * 64)
        s.add(doc)
        fact = remember_fact(s, owner=someone, content="Priya is planning a surprise party.", category="about")
        s.flush()
        return str(doc.id), str(fact.id)


def test_the_owner_cannot_remove_someone_elses_document_or_forget_their_facts(owner):
    doc_id, fact_id = _other_persons_rows()
    r = owner.post(f"/app/intro/documents/{doc_id}/remove")
    assert r.status_code == 404 and "priya" not in r.text.lower()
    r = owner.post(f"/app/notebook/{fact_id}/forget")
    assert r.status_code == 404 and "surprise" not in r.text
    with session_scope() as s:
        assert s.get(Document, uuid.UUID(doc_id)).removed_at is None
        assert s.get(MemoryItem, uuid.UUID(fact_id)).valid_to is None
    # And they never show up in the owner's lists.
    assert all(d["id"] != doc_id for d in owner.get("/app/intro/documents").json())
    assert all(m["id"] != fact_id for m in owner.get("/app/notebook").json()["memories"])


@pytest.mark.parametrize("path", ["/app/intro/documents/{}/remove", "/app/notebook/{}/forget"])
def test_ids_that_dont_exist_or_arent_ids(owner, path):
    assert owner.post(path.format(uuid.uuid4())).status_code == 404
    for bad in ("not-a-uuid", "1", "' OR 1=1 --", "00000000-0000-0000-0000-00000000000g"):
        assert owner.post(path.format(bad)).status_code == 422, bad


def test_removing_or_forgetting_twice_is_404_the_second_time(owner, owner_data):
    doc = f"/app/intro/documents/{owner_data['document_id']}/remove"
    assert owner.post(doc).status_code == 200
    assert owner.post(doc).status_code == 404
    forget = f"/app/notebook/{owner_data['memory_id']}/forget"
    assert owner.post(forget).status_code == 200
    assert owner.post(forget).status_code == 404


# --- the public "Ask my Ava" routes (no account) -------------------------------------------------------

PUBLIC_ASK = {("GET", "/p/ask/{handle}"), ("POST", "/p/ask/{handle}"), ("POST", "/p/ask/{handle}/click")}


def test_the_only_public_routes_outside_app_and_auth_are_the_ask_link(app):
    """A new public route has to be added here on purpose (and tested in tests/api/test_ask_link.py)."""
    assert set(app_routes(app, "/p")) == PUBLIC_ASK


def test_public_ask_routes_are_404_while_off_and_carry_no_owner_data(browser, owner_data):
    for method, path in sorted(PUBLIC_ASK):
        r = browser().request(method, path.replace("{handle}", "shashank"), json={} if method == "POST" else None)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert r.json() == {"detail": "Not Found"}
        _assert_no_owner_data(r, owner_data)


# --- Sign in with Google: public routes under /auth ---------------------------------------------------

GOOGLE_SIGNIN_ROUTES = {("GET", "/auth/google"), ("POST", "/auth/google/start")}  # + the hidden callback


def test_google_sign_in_routes_are_the_only_new_public_auth_routes(app):
    """Every /auth route either needs a session or is one of the known public ones (sign-up, log-in,
    email links, Google sign-in). A new /auth route has to be added here deliberately."""
    public = {
        ("POST", "/auth/signup"), ("POST", "/auth/login"), ("POST", "/auth/logout"), ("GET", "/auth/me"),
        ("POST", "/auth/verify-email"), ("POST", "/auth/password-reset/request"), ("POST", "/auth/password-reset"),
    } | GOOGLE_SIGNIN_ROUTES  # fmt: skip
    session_only = {
        ("POST", "/auth/password"),
        ("POST", "/auth/logout-everywhere"),
        ("POST", "/auth/verify-email/send"),
        ("POST", "/auth/email"),  # change email: needs the session and the password
    }
    assert set(app_routes(app, "/auth")) == public | session_only


def test_google_sign_in_gives_an_owner_s_data_to_nobody(browser, owner_data, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_signin_enabled", True)
    monkeypatch.setattr(settings, "google_client_id", "1234-signin.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "test-signin-secret-not-real")
    anonymous = browser()
    for method, path in sorted(GOOGLE_SIGNIN_ROUTES):
        r = anonymous.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 200, (method, path, r.text)
        _assert_no_owner_data(r, owner_data)
    r = anonymous.get("/auth/google/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
    assert r.status_code == 303 and "superaide_session" not in r.headers.get("set-cookie", "")
    assert anonymous.get("/app/me").status_code == 401


# --- the Gmail capability (/app/gmail) ---------------------------------------------------------

GMAIL_ROUTES = {
    ("GET", "/app/gmail"),
    ("POST", "/app/gmail/connect"),
    ("POST", "/app/gmail/drafts"),
    ("POST", "/app/gmail/addresses"),
}


def test_the_gmail_routes_are_owner_routes(app, browser, owner):
    assert GMAIL_ROUTES <= set(owner_routes(app))  # so every check above covers them too
    stranger = browser()
    signup(stranger, name="Mallory Tan", email="mallory@example.com")
    for method, path in GMAIL_ROUTES:
        assert browser().request(method, path, json={}).status_code == 401
        assert stranger.request(method, path, json={}).status_code == 403


def test_an_owner_cannot_attach_an_address_to_someone_who_isnt_their_contact(owner):
    with session_scope() as s:
        someone = Person(display_name="Priya Tan", relation="contact")  # a Person, but not the owner's contact
        s.add(someone)
        s.flush()
        pid = str(someone.id)
    r = owner.post("/app/gmail/addresses", json={"person_id": pid, "email": "priya@example.com"})
    assert r.status_code == 404


def test_calendar_actions_switch_is_an_owner_route(app, browser, owner_data):
    """POST /app/skills/{publisher}/{name}/options (calendar actions' consent) is covered by the
    enumerated 401/403 checks above; this pins it, and that a stranger can't switch it on."""
    path = "/app/skills/{publisher}/{name}/options"
    assert ("POST", path) in owner_routes(app)
    stranger = browser()
    signup(stranger, name="Mallory Tan", email="mallory@example.com")
    r = stranger.post("/app/skills/superaide/google-calendar/options", json={"setting": "actions", "on": True})
    assert r.status_code == 403
    _assert_no_owner_data(r, owner_data)
