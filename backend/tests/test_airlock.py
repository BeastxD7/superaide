"""Airlock (#128): other people's data is redacted before anything is uploaded to a model."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from superaide.brain.llm import FakeLLM, RoutedLLM, SystemBlock
from superaide.privacy import airlock

TEXT = (
    "Call Priya Tan on +65 8123 4567 or write to priya.tan@example.com. "
    "Her card 4242 4242 4242 4242 is on file, IBAN GB82 WEST 1234 5698 7654 32, SSN 123-45-6789, host 192.168.1.20, "
    "ping @priya_t. Priya said hi."
)


def test_contact_details_cards_and_ids_become_placeholders_without_the_original():
    r = airlock.redact(TEXT, names=["Priya Tan", "Priya"])
    for secret in ("8123 4567", "priya.tan@example.com", "4242 4242", "GB82", "123-45-6789", "192.168.1.20", "priya_t"):
        assert secret not in r.text, secret
    assert "Priya" not in r.text
    assert r.text.count("[person") == 2 and "[person 1]" in r.text and "[email 1]" in r.text and "[phone 1]" in r.text
    assert set(r.found) >= {"person", "email", "phone", "card", "iban", "national id", "ip address", "handle"}
    assert r.mapping["[email 1]"] == "priya.tan@example.com"
    assert "priya.tan@example.com" not in str(r.preview())  # the owner's preview names kinds, never values


def test_the_same_value_gets_the_same_placeholder_and_restore_round_trips():
    r = airlock.redact("a@x.com then b@x.com then a@x.com")
    assert r.text == "[email 1] then [email 2] then [email 1]"
    assert airlock.restore(r.text, r.mapping) == "a@x.com then b@x.com then a@x.com"


def test_the_owners_own_values_are_kept():
    r = airlock.redact("me: owner@example.com, them: other@example.com", keep=["owner@example.com"])
    assert "owner@example.com" in r.text and "other@example.com" not in r.text


def test_ordinary_numbers_dates_and_times_are_left_alone():
    plain = "Meet on 2026-10-23 at 10:30, invoice 1,250.00, 3 people, order 12345, version 1.2.3, call it 20261023"
    assert airlock.redact(plain).text == plain


def test_a_number_that_fails_the_card_check_is_not_a_card():
    r = airlock.redact("ref 4242 4242 4242 4241 ok")
    assert "card" not in r.found


def test_a_name_is_matched_whole_words_only_and_longest_first():
    r = airlock.redact("Anna met Annabel; Anna Lee left.", names=["Anna", "Anna Lee"])
    assert r.text == "[person 2] met Annabel; [person 1] left."


def test_messages_are_redacted_across_text_blocks_and_tool_results_without_touching_the_original():
    messages = [
        {"role": "user", "content": "Mail bob@x.com"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok bob@x.com"},
                {"type": "tool_use", "id": "t1", "name": "n", "input": {"to": "bob@x.com"}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "sent to bob@x.com"}]},
    ]
    out, r = airlock.redact_messages(messages)
    assert "bob@x.com" not in str(out[0]) + str(out[1]["content"][0]) + str(out[2])
    assert out[1]["content"][1]["input"] == {"to": "bob@x.com"}  # the model's own tool call is not rewritten
    assert messages[0]["content"] == "Mail bob@x.com"
    assert r.found == {"email": 3} and list(r.mapping) == ["[email 1]"]


class Reply(BaseModel):
    reply: str


def test_off_by_default_the_model_sees_the_conversation_unchanged(settings):
    llm = FakeLLM(lambda purpose, system, messages, out: "ok")
    RoutedLLM({"anthropic": llm}).complete(
        model="m",
        purpose="t",
        system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "mail a@x.com"}],
        max_tokens=50,
    )
    assert llm.calls[-1]["messages"][0]["content"] == "mail a@x.com"


def test_on_the_upload_is_clean_and_the_reply_gets_the_originals_back(monkeypatch, settings):
    monkeypatch.setattr(settings, "airlock_enabled", True)
    seen = {}

    def responder(purpose, system, messages, out):
        seen["messages"] = messages
        return Reply(reply="I will write to [email 1] now") if out else "I will write to [email 1] now"

    llm = FakeLLM(responder)
    routed = RoutedLLM({"anthropic": llm})
    res = routed.complete(
        model="m",
        purpose="t",
        system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "mail a@x.com"}],
        max_tokens=50,
    )
    assert "a@x.com" not in str(seen["messages"]) and "[email 1]" in str(seen["messages"])
    assert res.text == "I will write to a@x.com now"
    typed = routed.complete(
        model="m",
        purpose="t",
        system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "mail a@x.com"}],
        max_tokens=50,
        output_model=Reply,
    )
    assert isinstance(typed.parsed, Reply) and typed.parsed.reply == "I will write to a@x.com now"


@pytest.mark.parametrize("bad", ["", " ", "\n"])
def test_empty_text_is_fine(bad):
    assert airlock.redact(bad).text == bad
