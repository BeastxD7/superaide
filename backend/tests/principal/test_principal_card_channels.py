"""Principal Card on Telegram and Slack (#135): a model-written reply to someone who isn't the owner ends with
the card line and is noted in the owner's sent list; the owner's own chat never gets it; one card can be taken back."""

from __future__ import annotations

import base64
import re

import pytest

from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.principal import card
from tests.conftest import COLLEAGUE_SLACK, MANAGER_TG, sl, tg


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
    with session_scope() as s:
        card.configure(s, first_name="Ananya", initial="T", channels=True)


def test_a_colleague_reply_on_slack_carries_the_card_and_is_logged(agent, manager, colleague, outbox, model, on):
    model.reply = "Tuesday works."
    agent.handle(sl(COLLEAGUE_SLACK, "can we meet?"))
    text = outbox["slack"].sent[-1]["text"]
    assert text.startswith("Tuesday works.") and "AI assistant acting for Ananya T." in text
    token = re.search(r"/c/(\S+)", text).group(1).rstrip(">|)")
    with session_scope() as s:
        assert card.verify(s, token) is not None
        (entry,) = card.sent(s)
        assert entry["channel"] == "slack" and entry["to"] == "Priya Tan" and entry["status"] == "active"
        assert "meet" not in str(entry)  # never the message


def test_the_owners_own_chat_never_carries_it(agent, manager, outbox, model, on):
    model.reply = "Done."
    agent.handle(tg(MANAGER_TG, "hi"))
    assert "AI assistant acting for" not in outbox["telegram"].sent[-1]["text"]
    with session_scope() as s:
        assert card.sent(s) == []


def test_off_means_no_line(agent, manager, colleague, outbox, model, monkeypatch):
    monkeypatch.setattr(get_settings(), "principal_card_enabled", False)
    agent.handle(sl(COLLEAGUE_SLACK, "can we meet?"))
    assert "AI assistant acting for" not in outbox["slack"].sent[-1]["text"]


def test_one_card_can_be_taken_back_and_the_rest_stay_valid(agent, manager, colleague, outbox, model, on):
    for _ in range(2):
        agent.handle(sl(COLLEAGUE_SLACK, "again?"))
    tokens = [re.search(r"/c/([^\s>|)]+)", m["text"]).group(1) for m in outbox["slack"].sent]
    with session_scope() as s:
        first, second = card.sent(s)[1], card.sent(s)[0]
        assert card.revoke_one(s, first["id"]) and not card.revoke_one(s, "nope")
        assert [e["status"] for e in card.sent(s)] == ["active", "revoked"]
        assert card.verify(s, tokens[0]) is None and card.verify(s, tokens[1]) is not None
        assert second["id"] != first["id"]
