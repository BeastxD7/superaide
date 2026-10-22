"""The person-facing app: hire, onboarding, chat, notebook, connecting Telegram.

Every client here is a browser: it signs up (getting the session cookie) and
sends X-Requested-With on changes, like the real frontend does.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app import accounts
from superaide.db.models import ChannelIdentity, Contact, OwnerPreference, Person
from superaide.db.session import session_scope
from superaide.main import create_app
from tests.conftest import sl, tg

PASSWORD = "correct horse battery"


@pytest.fixture
def browser(agent):
    """Make a fresh browser (its own cookie jar) talking to the same app."""
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


@pytest.fixture
def client(browser):
    return browser()


def _signup(client, name="Shashank Rao", email="shashank@example.com"):
    r = client.post("/auth/signup", json={"name": name, "email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _hire(client, name="Shashank Rao", email="shashank@example.com"):
    _signup(client, name, email)
    r = client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    assert r.status_code == 200, r.text
    return {}


def _answer(client, auth, step, **answer):
    r = client.post("/app/onboarding", json={"step": step, "answer": answer})
    assert r.status_code == 200, r.text
    return r.json()


def test_marketplace_lists_ava_as_available(client):
    agents = client.get("/app/agents").json()
    ava = next(a for a in agents if a["slug"] == "ava")
    assert ava["status"] == "available" and ava["hired"] is False and "hired_by" not in ava
    assert any(a["status"] == "coming_soon" for a in agents)


def test_hiring_needs_an_account_and_consent_and_everyone_gets_their_own(client, browser):
    assert client.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 401
    _signup(client)
    assert client.post("/app/hire", json={"agent": "ava", "accepted_notice": False}).status_code == 400
    assert client.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    assert client.get("/auth/me").json()["hire"]["agent"] == "ava"
    # Someone else hires their own Ava; it's never shown as taken.
    other = browser()
    _signup(other, "Priya Tan", "priya@example.com")
    assert other.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    assert other.get("/app/me").json()["person"]["name"] == "Priya Tan"
    assert client.get("/app/me").json()["person"]["name"] == "Shashank Rao"
    ava = client.get("/app/agents").json()[0]
    assert ava["hired"] is False and "hired_by" not in ava
    assert "Shashank" not in other.get("/app/agents").text


def test_in_single_owner_mode_hiring_happens_once(client, browser, monkeypatch):
    from superaide.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_many_owners", False)
    _signup(client)
    assert client.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    # Someone else can make an account, but Ava already has a manager.
    other = browser()
    _signup(other, "Priya Tan", "priya@example.com")
    assert other.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 409
    assert other.get("/app/me").status_code == 403  # and can't see Ava's workspace
    ava = client.get("/app/agents").json()[0]
    assert ava["hired"] is True and "hired_by" not in ava  # taken, but never by whom
    assert "Shashank" not in client.get("/app/agents").text


def test_app_needs_a_session(client):
    assert client.get("/app/me").status_code == 401
    client.cookies.set("superaide_session", "forged-token")
    assert client.get("/app/me").status_code == 401


def test_full_introduction_then_chat(client, model):
    _hire(client)
    me = client.get("/app/me").json()
    assert me["onboarding"] == {"step": "hello", "done": False}
    assert me["agent"]["role"] == "Personal assistant"
    first = client.get("/app/messages").json()
    assert first[0]["from"] == "agent" and "your personal assistant" in first[0]["text"]

    view = _answer(client, None, "hello", choice="What will you do for me?")
    assert view["step"] == "about" and view["input"]["kind"] == "intro"
    assert _answer(client, None, "about", mode="continue")["hint"]  # nothing shared yet
    assert _answer(client, None, "about", mode="chat", text="")["step"] == "about"
    view = _answer(client, None, "about", mode="chat", text="I run an AI consultancy in Singapore.")
    assert view["step"] == "people"
    view = _answer(
        client,
        None,
        "people",
        people=[
            {"name": "Priya", "relationship": "Partner", "vip": True},
            {"name": "Arjun", "relationship": "Team", "vip": False},
            {"name": "  ", "relationship": "Team"},
        ],
    )
    assert view["step"] == "urgent"
    view = _answer(
        client, None, "urgent", topics=["A family emergency"], other="Anything about the Series A", digest="morning"
    )
    assert view["step"] == "reach"
    assert _answer(client, None, "reach", quiet_start="25:00", quiet_end="07:00")["hint"]  # bad time
    view = _answer(
        client, None, "reach", channel="telegram", quiet_start="22:30", quiet_end="07:00",
        vip_breaks_quiet=True, timezone="Asia/Singapore",
    )  # fmt: skip
    assert view["step"] == "share"
    view = _answer(client, None, "share", shareable=["availability", "work_email", "nonsense"], never="My home address")
    assert view["step"] == "rules"
    view = _answer(client, None, "rules")
    assert view["step"] == "connect" and view["input"]["telegram_available"] is False
    view = _answer(client, None, "connect", choice="done")
    assert view["done"] is True

    # The settings landed in typed tables.
    with session_scope() as s:
        pref = s.scalar(select(OwnerPreference))
        assert (pref.digest, pref.interrupt_channel, pref.quiet_start) == ("morning", "app", "22:30")  # no Telegram yet
        assert pref.shareable == ["availability", "work_email"] and pref.never_share == "My home address"
        assert "Anything about the Series A" in pref.urgent_topics
        people = s.execute(select(Contact, Person).join(Person, Person.id == Contact.person_id)).all()
        assert {(p.display_name, c.vip, p.relation) for c, p in people} == {
            ("Priya", True, "contact"),
            ("Arjun", False, "contact"),
        }
    # And as facts with their category and source.
    notebook = client.get("/app/notebook").json()["memories"]
    assert {"about", "person", "urgency", "availability", "sharing"} <= {m["category"] for m in notebook}
    assert {m["source"] for m in notebook} == {"intro_chat"}
    assert all(m["private"] for m in notebook)
    convo = client.get("/app/messages").json()
    assert convo[-1]["kind"] == "onboarding" and "So far I know" in convo[-1]["text"]

    # Normal chat afterwards, with what Ava learned in its prompt.
    model.reply = "Priya is your partner."
    r = client.post("/app/chat", json={"text": "Who is Priya?"}).json()
    assert r["mode"] == "known" and r["reply"] == "Priya is your partner."
    assert "Priya" in model.systems[-1]


def test_notebook_is_private_and_forgettable(client, agent, model, colleague):
    _hire(client)
    _answer(client, None, "hello", choice="Nice to meet you")
    _answer(client, None, "about", mode="chat", text="I build an AI agent marketplace.")
    notebook = client.get("/app/notebook").json()["memories"]
    assert any("AI agent marketplace" in m["text"] for m in notebook)

    # A colleague asking in their own DM never sees it.
    agent.handle(sl("U_PRIYA", "What is Shashank building?"))
    assert "AI agent marketplace" not in model.systems[-1]

    mem = next(m for m in notebook if "AI agent marketplace" in m["text"])
    assert client.post(f"/app/notebook/{mem['id']}/forget").status_code == 200
    notebook = client.get("/app/notebook").json()["memories"]
    assert not any("AI agent marketplace" in m["text"] for m in notebook)


def test_telegram_invite_links_the_phone_to_the_app_account(client, agent):
    _hire(client)
    with session_scope() as s:
        person = s.scalar(select(Person).where(Person.relation == "manager"))
        code = accounts.create_invite(s, person, "telegram", endpoint="")
    # Someone without the link stays a stranger.
    assert agent.handle(tg("777000", "/start wrongcode123")).mode == "stranger"
    # Opening the link sends "/start <code>" from the person's real Telegram.
    result = agent.handle(tg("777001", f"/start {code}"))
    assert "Linked" in (result.reply or "")
    assert agent.handle(tg("777001", "hello again")).mode == "known"
    # The link works only once.
    assert "expired or was already used" in (agent.handle(tg("777002", f"/start {code}")).reply or "")
    assert client.get("/app/me").json()["channels"]["telegram"] is True
    with session_scope() as s:
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "777001"))
        assert identity.verification["method"] == "invite_from_app"


def test_log_out_ends_the_session(client):
    _hire(client)
    assert client.get("/app/me").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/app/me").status_code == 401


def test_introduction_is_said_once(client):
    _hire(client)
    first = client.get("/app/messages").json()
    assert first and first[0]["from"] == "agent" and "I'm Ava" in first[0]["text"]
    client.get("/app/onboarding")  # opening it again doesn't repeat itself
    client.get("/app/onboarding")
    assert len(client.get("/app/messages").json()) == len(first)


def test_backend_is_an_api_for_the_frontend_on_port_3000(client):
    """The backend serves JSON only; the React app on :3000 calls it across origins."""
    assert client.get("/").json()["service"] == "superaide API"
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        r = client.options(
            "/app/agents",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-requested-with,content-type",
            },
        )
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
        assert r.headers["access-control-allow-credentials"] == "true"
    r = client.options("/app/agents", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("A family emergency", "a family emergency"),
        ("Anything about the Series A", "anything about the Series A"),
        ("AWS outages", "AWS outages"),
        ("PebbleRoad clients", "PebbleRoad clients"),
        ("Priya's birthday", "Priya's birthday"),
        ("Legal, money or contracts", "legal, money or contracts"),
        ("", ""),
    ],
)
def test_urgent_topics_keep_names_mid_sentence(topic, expected):
    from superaide.app.onboarding import _mid_sentence

    assert _mid_sentence(topic) == expected


def test_a_blank_chat_message_is_refused_instead_of_crashing(client):
    _hire(client)
    r = client.post("/app/chat", json={"text": "   "})
    assert r.status_code == 422 and r.json()["detail"] == "Type a message first."


def test_remember_that_keeps_one_note_and_ignores_an_empty_one():
    from superaide.memory.store import parse_remember

    assert parse_remember("Remember that") is None
    assert parse_remember("remember this.") is None
    assert parse_remember("remember that my flight is Friday") == "my flight is Friday"


def test_saying_the_same_thing_twice_keeps_one_note(agent, manager):
    from sqlalchemy import func, select

    from superaide.db.models import MemoryItem
    from tests.conftest import MANAGER_TG, tg

    agent.handle(tg(MANAGER_TG, "remember that my flight is on Friday"))
    agent.handle(tg(MANAGER_TG, "Remember that  my flight is on friday."))
    agent.handle(tg(MANAGER_TG, "Remember that"))
    with session_scope() as s:
        notes = s.scalars(select(MemoryItem.content)).all()
        assert notes == ["my flight is on Friday"]
        assert s.scalar(select(func.count()).select_from(MemoryItem)) == 1
