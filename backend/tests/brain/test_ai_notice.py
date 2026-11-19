"""The owner is told, specifically and only the owner, when the AI cannot be used for a configuration reason:
no key, key rejected, credit too low, spend cap reached (#633). Visitors keep the generic line."""

from __future__ import annotations

import pytest

from superaide import doctor
from superaide.brain import keyhealth
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

SECRET = "sk-ant-api03-" + "z" * 40
ADMIN = {"Authorization": "Bearer test-admin-token"}


class BadRequestError(Exception):
    """What the Anthropic SDK raises for a 400; same shape: status_code and a body."""

    status_code = 400

    def __init__(self, message: str = "Your credit balance is too low to access the Anthropic API.") -> None:
        super().__init__(f"Error code: 400 - {message}")
        self.body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


class Denied(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"Error code: {code} - denied {SECRET}")
        self.status_code = code


def failing(agent, exc: Exception) -> None:
    def responder(purpose, system, messages, output_model):
        raise exc

    agent.llm.responder = responder


def notice() -> dict | None:
    with session_scope() as s:
        return keyhealth.notice(s, get_settings())


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (BadRequestError(), keyhealth.CREDIT_LOW),
        (Denied(402), keyhealth.CREDIT_LOW),
        (Denied(401), keyhealth.KEY_INVALID),
        (Denied(403), keyhealth.KEY_INVALID),
        (BadRequestError("messages: text content blocks must be non-empty"), None),
        (Denied(429), None),
        (Denied(500), None),
        (ConnectionError("reset"), None),
    ],
)
def test_provider_errors_are_classified(exc, reason):
    assert keyhealth.classify(exc) == reason


def test_the_spend_cap_is_classified():
    from superaide.brain.spend.budget import BudgetExceeded

    assert keyhealth.classify(BudgetExceeded("cap", "month")) == keyhealth.SPEND_CAP


def test_credit_too_low_tells_the_owner_what_to_do_and_raises_the_banner(owner_client, agent):  # noqa: F811
    failing(agent, BadRequestError())
    r = owner_client.post("/app/chat", json={"text": "hello"})
    assert r.status_code == 200, r.text
    assert "out of credit" in r.text and "Add credit at the provider" in r.text
    assert SECRET not in r.text and "couldn't think that through" not in r.text
    me = owner_client.get("/app/me").json()
    assert me["ai_notice"]["reason"] == "credit_low"
    assert me["ai_notice"]["href"] == "/dashboard/models"
    assert "Anthropic" not in str(me["ai_notice"])


def test_a_rejected_key_raises_the_banner_without_the_key(owner_client, agent):  # noqa: F811
    failing(agent, Denied(401))
    r = owner_client.post("/app/chat", json={"text": "hello"})
    assert "key was rejected" in r.text and SECRET not in r.text
    me = owner_client.get("/app/me")
    assert me.json()["ai_notice"]["reason"] == "key_invalid" and SECRET not in me.text


def test_no_key_tells_the_owner_instead_of_echoing(owner_client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(keyhealth, "missing", lambda settings: True)
    r = owner_client.post("/app/chat", json={"text": "hello"})
    assert "I have no AI key yet" in r.text and "Offline mode" not in r.text
    assert owner_client.get("/app/me").json()["ai_notice"]["reason"] == "key_missing"


def test_a_working_model_shows_no_notice(owner_client):  # noqa: F811
    assert owner_client.get("/app/me").json()["ai_notice"] is None


def test_the_spend_cap_reply_links_to_the_page_and_raises_the_banner(owner_client, agent):  # noqa: F811
    from superaide.brain.spend.budget import BudgetExceeded

    failing(agent, BudgetExceeded("cap", "lifetime"))
    r = owner_client.post("/app/chat", json={"text": "hello"})
    assert "Raise the cap on the AI model page" in r.text
    assert owner_client.get("/app/me").json()["ai_notice"]["reason"] == "spend_cap"


def test_visitors_and_colleagues_get_the_generic_line_and_the_owner_a_note(owner_client, agent):  # noqa: F811
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        boss = hired_owner(s)
        assert boss is not None
        told = agent._key_rejected(s, BadRequestError(), owner=False)
    assert told == keyhealth.OTHERS_CHAT
    for word in ("credit", "billing", "key", "Anthropic", "provider"):
        assert word.lower() not in (told or "").lower()


def test_check_my_setup_and_the_console_gaps_show_it(owner_client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(keyhealth, "missing", lambda settings: True)
    body = owner_client.post("/app/setup-check").json()
    row = next(x for x in body["results"] if x["name"] == "AI key")
    assert row["status"] == "fail" and "AI model page" in row["fix"]
    gaps = owner_client.get("/api/overview", headers=ADMIN).json()["gaps"]
    assert any(g["id"] == "ai_key_missing" for g in gaps)
    assert SECRET not in str(body) + str(gaps)


def test_the_doctor_check_is_ok_when_nothing_is_wrong(owner_client):  # noqa: F811
    results = doctor.run_in_app(get_settings())
    assert not [r for r in results if r.name == "AI key" and r.status != doctor.OK]


def test_startup_logs_an_error_and_never_raises(monkeypatch, caplog):
    monkeypatch.setattr(keyhealth, "missing", lambda settings: True)
    with caplog.at_level("ERROR"):
        keyhealth.warn_if_missing(get_settings())
    assert any("No AI key is set" in r.message for r in caplog.records)

    def boom(settings):
        raise RuntimeError("db down")

    monkeypatch.setattr(keyhealth, "missing", boom)
    keyhealth.warn_if_missing(get_settings())  # no exception


def test_a_new_key_clears_the_credit_trouble(owner_client):  # noqa: F811
    keyhealth.record_trouble(keyhealth.CREDIT_LOW, "anthropic")
    assert notice() is not None
    with session_scope() as s:
        keyhealth.reset(s)
    assert notice() is None
