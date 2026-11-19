"""#638: notes found before the model runs reach every provider, not only Claude.

Claude gets them as native `search_result` blocks; an OpenAI-compatible model (Ollama, OpenAI, Gemini,
OpenRouter) gets the same notes as `[source id=... title=...]` text it can quote and cite."""

from __future__ import annotations

from typing import Any

import pytest

from superaide.brain.llm import ProviderLLM
from superaide.brain.providers.anthropic import messages_from_wire, messages_to_wire
from superaide.brain.providers.openai_compat import to_chat_messages
from superaide.brain.spend.pricing import PriceRegistry
from superaide.memory.audience import Audience
from superaide.platform.db.session import session_scope
from tests.brain.test_openai_compat import PRICE, Server, completion, provider
from tests.pipeline.test_loop import note, owner_client, turn_for  # noqa: F401  (owner_client is a fixture)

LEAD = "(Notes from your notebook that may be relevant, found automatically:)"


def _wire() -> list[dict[str, Any]]:
    def block(uri: str, title: str, text: str) -> dict[str, Any]:
        return {
            "type": "search_result", "source": uri, "title": title,
            "content": [{"type": "text", "text": text}], "citations": {"enabled": True},
        }  # fmt: skip

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": LEAD},
                block("ava://memory/1", "Cuisine", "Jordan's favourite cuisine is Thai food."),
                block("ava://memory/2", "Flight", "Flight to Bangalore on Friday at 7am."),
                {"type": "text", "text": "what is my favourite cuisine?"},
            ],
        }
    ]


def test_two_pre_recalled_notes_survive_the_openai_format():
    out = to_chat_messages("", messages_from_wire(_wire()))
    assert len(out) == 1 and out[0]["role"] == "user"
    body = out[0]["content"]
    assert "Jordan's favourite cuisine is Thai food." in body and "ava://memory/1" in body
    assert "Flight to Bangalore on Friday at 7am." in body and "ava://memory/2" in body
    assert body.index(LEAD) < body.index("ava://memory/1") < body.rindex("what is my favourite cuisine?")


def test_claude_gets_exactly_the_blocks_it_got_before():
    wire = _wire()
    assert messages_to_wire(messages_from_wire(wire)) == wire


@pytest.mark.usefixtures("owner_client")
def test_an_openai_compatible_turn_sends_the_note_and_can_cite_it():
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "Jordan's favourite cuisine is Thai food.")
        mid = m.id  # the fake server reports 10 prompt tokens, which the turn notes as a cut prompt (expires m)
        server = Server(completion(f"Thai food [ava://memory/{m.id}]."))
        p, _ = provider(server, prices=PriceRegistry({("openai", "claude-haiku-5-5"): PRICE}))
        turn = turn_for(s, lambda *a: None, text="what is my favourite cuisine?")
        turn.llm = ProviderLLM(p)
        result = turn.run([{"role": "user", "content": "what is my favourite cuisine?"}])
    sent = [x for x in server.requests[0]["messages"] if x["role"] == "user"][-1]["content"]
    assert LEAD in sent and "Jordan's favourite cuisine is Thai food." in sent and f"ava://memory/{mid}" in sent
    assert [c.uri for c in result.sources] == [f"ava://memory/{mid}"]  # the reply's citation resolves
    assert result.text.startswith("Thai food [1].")


@pytest.mark.usefixtures("owner_client")
def test_no_matching_note_means_no_lead_line_either():
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        note(s, person.id, "Jordan's favourite cuisine is Thai food.")
        server = Server(completion("Hello."))
        p, _ = provider(server, prices=PriceRegistry({("openai", "claude-haiku-5-5"): PRICE}))
        turn = turn_for(s, lambda *a: None, text="tell me a joke")
        turn.llm = ProviderLLM(p)
        turn.run([{"role": "user", "content": "tell me a joke"}])
    sent = [x for x in server.requests[0]["messages"] if x["role"] == "user"][-1]["content"]
    assert LEAD not in sent and "[source" not in sent


@pytest.mark.usefixtures("owner_client")
def test_an_owner_only_note_never_reaches_a_turn_for_someone_else(colleague):
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        note(s, person.id, "Jordan's favourite cuisine is Thai food.")
        server = Server(completion("I don't know."))
        p, _ = provider(server, prices=PriceRegistry({("openai", "claude-haiku-5-5"): PRICE}))
        turn = turn_for(s, lambda *a: None, text="what is jordan's favourite cuisine?")
        for who in (Audience(person_ids={colleague.id}, min_trust=1), Audience(has_unknown=True)):
            turn.aud = who
            assert turn._with_notes([{"role": "user", "content": "what is jordan's favourite cuisine?"}]) == [
                {"role": "user", "content": "what is jordan's favourite cuisine?"}
            ]
