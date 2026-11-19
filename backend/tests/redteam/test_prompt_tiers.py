# ci-module: brain
"""A smaller prompt never widens what a person may see or what may run (#641).

The tier is picked from the window and the words alone, and only ever removes or shortens what the loop already
built for the person in the chat. These are the ways that could go wrong: a shared channel gaining a private tool,
a private note reaching a room, or a tier reading something about the sender."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from superaide.brain import prompt_tiers
from superaide.brain.llm import FakeLLM
from superaide.brain.loop import SHARED_TOOLS
from superaide.brain.models import LlmProvider
from superaide.memory.audience import audience_for
from superaide.memory.models import Context
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, note, owner_client, tool_use, turn_for  # noqa: F401  (fixture)

SECRET = "The safe code is 4417, for Jordan only."
WINDOWS = (200_000, 32_000, 16_384, 8_000, 4_096)
PRIVATE_WORDS = "email my calendar and reminders, tell Meera I'm busy, who is waiting in the inbox, send it"


def local_model(s, window: int) -> str:
    row = s.scalar(select(LlmProvider).where(LlmProvider.key == "ollama"))
    if row is None:
        row = LlmProvider(key="ollama", preset="ollama", base_url="http://localhost:11434/v1")
        s.add(row)
    row.meta = {"context_window": window}
    s.flush()
    return "ollama::qwen3:8b"


def channel_turn(s, window: int, text: str, script: Script) -> Any:
    """The owner speaking in a channel others can read: public-level audience, shared tools."""
    turn = turn_for(s, script, text=text)
    ctx = Context(provider="slack", tenant_scope="T", external_id="C9", kind="group", audience_class="team")
    s.add(ctx)
    s.flush()
    turn.aud, turn.shared = audience_for(ctx, turn.sender), True
    turn.model, turn.llm = local_model(s, window), FakeLLM(script)
    return turn


@pytest.mark.parametrize("window", WINDOWS)
def test_a_channel_turn_keeps_the_shared_tools_exactly_in_every_tier(owner_client, window):  # noqa: F811
    with session_scope() as s:
        turn = channel_turn(s, window, PRIVATE_WORDS, Script("ok"))
        turn.run([{"role": "user", "content": PRIVATE_WORDS}])
        call = turn.llm.calls[0]
        offered = {t["name"] for t in call["tools"]}
        assert offered <= SHARED_TOOLS | {"answer_in_dm"}, offered - SHARED_TOOLS
        text = "\n".join(b.text for b in call["system"])
        assert "not a private chat" in text, "the channel rules are in every tier"
        if window < 100_000:
            assert not {"gmail_search", "list_reminders", "relay_message", "set_presence", "inbox"} & offered
            assert "gmail_" not in text and "relay_message" not in text and "set_presence" not in text


@pytest.mark.parametrize("window", WINDOWS)
def test_a_private_tool_called_in_a_channel_is_refused_in_every_tier(owner_client, window):  # noqa: F811
    with session_scope() as s:
        script = Script(tool_use("gmail_search", {"query": "from:bank"}, "t1"), "ok")
        turn = channel_turn(s, window, "any mail from the bank?", script)
        turn.run([{"role": "user", "content": "any mail from the bank?"}])
        assert script.seen[1][-1]["content"][0].get("is_error") is True


@pytest.mark.parametrize("window", WINDOWS)
def test_a_private_note_never_reaches_a_channel_prompt_in_any_tier(owner_client, window):  # noqa: F811
    with session_scope() as s:
        turn = channel_turn(s, window, "what is the safe code?", Script("ok"))
        assert turn.sender.person is not None
        note(s, turn.sender.person.id, SECRET)  # visible to the owner alone
        turn.run([{"role": "user", "content": "what is the safe code?"}])
        assert "4417" not in json.dumps([{**c, "system": [b.text for b in c["system"]]} for c in turn.llm.calls])


def test_the_same_note_is_in_the_owners_private_prompt_so_the_test_above_can_fail(owner_client):  # noqa: F811
    with session_scope() as s:
        turn = turn_for(s, Script("ok"), text="what is the safe code?")
        assert turn.sender.person is not None
        note(s, turn.sender.person.id, SECRET)
        turn.model, turn.llm = local_model(s, 200_000), FakeLLM(Script("ok"))
        turn.run([{"role": "user", "content": "what is the safe code?"}])
        assert "4417" in json.dumps(turn.llm.calls[0]["messages"])


def test_the_tier_reads_nothing_about_the_sender():
    """select_tier gets a window, the words and a flag: no person, trust or audience, so it cannot treat people
    differently; and the module imports nothing that could look one up."""
    assert list(inspect.signature(prompt_tiers.select_tier).parameters) == ["window", "text", "pending"]
    tree = ast.parse(Path(prompt_tiers.__file__).read_text())
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in getattr(n, "names", [])
    }
    forbidden = ("identity", "memory", "approvals", "policy", "reception", "sqlalchemy", "platform")
    assert not [m for m in imported if any(f in m for f in forbidden)], imported
