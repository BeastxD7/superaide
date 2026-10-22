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
FLAG_GATED = {("GET", "/app/needs-you"), ("GET", "/app/triage/settings")}
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
