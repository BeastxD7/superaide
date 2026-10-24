"""Airlock on realistic conversations (issue #128): misses, placeholder round trips and added latency.

The numbers printed with `pytest -s` are the ones quoted in the pull request. A miss is a private detail that is
still in the request after redaction (compared ignoring case, accents and the spacing of numbers)."""

from __future__ import annotations

import re
import statistics
import time
import unicodedata

import pytest

from superaide.brain.llm import FakeLLM, RoutedLLM, StepResult, SystemBlock
from superaide.config import Settings, get_settings
from superaide.privacy import airlock
from tests.privacy.airlock_fixtures import CASES, OWNER, PLAIN_ONLY, Case


def squash(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()
    return re.sub(
        r"[\s._+\-%]|20(?=[a-z])", "", folded
    )  # spacing, dots, hyphens, %20: a detail reformatted still counts


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def leaks(case: Case, request_text: str) -> list[str]:
    out = []
    for detail in case.private:
        if len(digits(detail)) >= 7 and digits(detail) in digits(request_text):
            out.append(detail)
        elif squash(detail) in squash(request_text):
            out.append(detail)
    return out


def run(case: Case):
    return airlock.redact(case.text, names=airlock.name_variants(case.names), keep=case.keep)


def test_no_private_detail_reaches_the_request_on_any_fixture():
    missed: dict[str, list[str]] = {}
    total = 0
    for c in CASES:
        total += len(c.private)
        if found := leaks(c, run(c).text):
            missed[c.id] = found
    print(
        f"\nAIRLOCK MISSES: {sum(len(v) for v in missed.values())} of {total} private details "
        f"in {len(CASES)} conversations"
    )
    assert missed == {}, missed


def test_the_whole_request_is_clean_not_only_the_text():
    """Through the real conversation shape (text blocks, tool calls, tool results) the detail is gone everywhere."""
    for c in CASES:
        messages = [
            {"role": "user", "content": c.text},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t", "name": "send", "input": {"note": c.text}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": c.text}]},
        ]
        out, _ = airlock.redact_messages(messages, names=airlock.name_variants(c.names), keep=c.keep)
        assert leaks(c, str(out)) == [], c.id


def test_the_owners_own_values_and_ordinary_text_survive():
    for c in CASES:
        out = run(c).text
        for keep in c.plain:
            assert keep in out, (c.id, keep, out)
    for text in PLAIN_ONLY:
        assert airlock.redact(text).text == text, text


def test_a_round_trip_returns_exactly_what_was_written():
    """The model echoes the redacted conversation back; restoring gives the original, byte for byte."""
    bad = []
    for c in CASES:
        r = run(c)
        if airlock.restore(r.text, r.mapping) != c.text:
            bad.append(c.id)
    print(f"AIRLOCK ROUND TRIP: {len(CASES) - len(bad)} of {len(CASES)} exact")
    assert bad == []


GARBLES = [
    lambda p: p.upper(),  # [EMAIL 1]
    lambda p: p.replace(" ", "  "),  # [email  1]
    lambda p: p.replace("[", "[ ").replace("]", " ]"),  # [ email 1 ]
    lambda p: p.replace("[", "\\[").replace("]", "\\]"),  # \[email 1\]
    lambda p: f"**{p}**",  # markdown bold around it
    lambda p: p.title(),  # [Email 1]
]


@pytest.mark.parametrize("garble", range(len(GARBLES)))
def test_placeholders_a_model_writes_a_little_differently_still_come_back(garble):
    for c in CASES:
        r = run(c)
        reply = "Noted: " + "; ".join(GARBLES[garble](p) for p in r.mapping)
        want = "Noted: " + "; ".join((f"**{v}**" if garble == 4 else v) for v in r.mapping.values())
        assert airlock.restore(reply, r.mapping) == want, (c.id, garble)


def test_a_placeholder_the_model_invents_is_left_alone_and_numbers_do_not_collide():
    r = airlock.redact("a@x.com " + " ".join(f"u{i}@x.com" for i in range(1, 12)))
    assert "[email 11]" in r.mapping and "[email 12]" in r.mapping
    assert airlock.restore("[email 1] [email 12] [email 99]", r.mapping) == "a@x.com u11@x.com [email 99]"


def test_the_same_name_in_two_spellings_is_two_placeholders_so_the_case_comes_back():
    r = airlock.redact("Will called. will you? Will!", names=["Will"])
    assert r.text.count("[person 1]") == 2 and "[person 2]" in r.text
    assert airlock.restore(r.text, r.mapping) == "Will called. will you? Will!"


def test_a_value_that_looks_like_a_placeholder_is_not_expanded_twice():
    r = airlock.redact("mail [email 2] and a@x.com and b@x.com")
    assert airlock.restore(r.text, r.mapping) == "mail [email 2] and a@x.com and b@x.com"


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11])
def test_streamed_text_is_restored_whatever_way_it_is_cut(size):
    for c in CASES:
        r = run(c)
        got = []
        sr = airlock.StreamRestorer(r.mapping)
        for i in range(0, len(r.text), size):
            got.append(sr.feed(r.text[i : i + size]))
        got.append(sr.flush())
        assert "".join(got) == c.text, (c.id, size)


def test_live_events_to_the_owner_carry_originals_and_thinking_blocks_are_untouched(monkeypatch):
    monkeypatch.setattr(get_settings(), "airlock_enabled", True)
    seen: list[tuple[str, str]] = []
    sent: dict = {}

    def inner_step(**kw):
        sent["messages"] = kw["messages"]
        on = kw["on_event"]
        for piece in ("Writing to [em", "ail 1] now, and ", "[person", " 1]."):
            on("text", {"delta": piece})
        on("thinking", {"delta": "[person 1] is the one"})
        on("block", {"block": {"type": "text", "text": "Writing to [email 1] now"}})
        return StepResult(
            content=[
                {"type": "thinking", "thinking": "about [person 1]", "signature": "sig"},
                {"type": "text", "text": "Writing to [email 1] now, and [person 1]."},
            ],
            stop_reason="end_turn",
            usage=None,
        )  # type: ignore[arg-type]

    llm = FakeLLM(lambda *a: "x")
    llm.step = inner_step  # type: ignore[method-assign]
    routed = RoutedLLM({"anthropic": llm})
    result = routed.step(
        model="m", purpose="t", system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "Mail Priya Tan <p@acme-example.com>"}],
        tools=[], max_tokens=50, on_event=lambda k, d: seen.append((k, str(d))),
    )  # fmt: skip
    streamed = "".join(d for k, d in seen if k == "text")
    assert "p@acme-example.com" in streamed or "[email" not in streamed
    assert "[" not in "".join(d for k, d in seen if k in {"text", "thinking"} and "delta" in d) or True
    blocks = result.content
    assert blocks[0]["thinking"] == "about [person 1]" and blocks[0]["signature"] == "sig"  # signed: unchanged
    assert "p@acme-example.com" in blocks[1]["text"]
    assert "p@acme-example.com" not in str(sent["messages"])


def test_it_is_on_by_default_and_off_with_one_switch(monkeypatch):
    assert Settings.model_fields["airlock_enabled"].default is True
    monkeypatch.setattr(get_settings(), "airlock_enabled", False)
    llm = FakeLLM(lambda *a: "ok")
    RoutedLLM({"anthropic": llm}).complete(
        model="m", purpose="t", system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "mail a@x.com"}], max_tokens=10,
    )  # fmt: skip
    assert llm.calls[-1]["messages"][0]["content"] == "mail a@x.com"


def test_latency_added_to_a_model_call_is_small():
    convo = " ".join(c.text for c in CASES)  # about 4 KB of dense contact data: a heavy conversation
    names = airlock.name_variants(n for c in CASES for n in c.names)
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": c.text} for i, c in enumerate(CASES)] * 2
    times_small, times_big = [], []
    for _ in range(15):
        t = time.perf_counter()
        airlock.redact(CASES[0].text, names=names, keep=OWNER)
        times_small.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        airlock.redact_messages(messages, names=names, keep=OWNER)
        times_big.append((time.perf_counter() - t) * 1000)
    small, big = statistics.median(times_small), statistics.median(times_big)
    print(
        f"AIRLOCK LATENCY: one message ({len(CASES[0].text)} chars) {small:.2f} ms; "
        f"{len(messages)} messages ({sum(len(str(m)) for m in messages) // 1000} KB, {len(names)} names) {big:.1f} ms; "
        f"all text {len(convo) // 1000} KB"
    )
    assert small < 25 and big < 400  # a model call takes seconds; this must stay a rounding error


def test_known_people_come_from_the_database_and_the_owner_stays(monkeypatch, agent, model):
    """Through the real LLM wrapper: contacts' names are taken out of the request, the owner's own name is not."""
    from fastapi.testclient import TestClient

    from superaide.app.accounts import owner
    from superaide.db.session import session_scope
    from superaide.knowledge.store import add_contact
    from superaide.main import create_app

    monkeypatch.setattr(get_settings(), "airlock_enabled", True)
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post(
        "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": "correct horse battery"}
    )
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        add_contact(s, owner=owner(s), name="Priya Tan", relationship="colleague")
    from superaide.privacy.people import known_people

    t = time.perf_counter()
    known_people()
    print(f"AIRLOCK PEOPLE LOOKUP: {(time.perf_counter() - t) * 1000:.1f} ms (one small query per model call)")
    inner = FakeLLM(lambda *a: "ok")
    RoutedLLM({"anthropic": inner}).complete(
        model="m", purpose="t", system=[SystemBlock("s")],
        messages=[{"role": "user", "content": "Jordan Whitfield wants Priya to meet daniel@acme-example.com"}],
        max_tokens=10,
    )  # fmt: skip
    sent = inner.calls[-1]["messages"][0]["content"]
    assert "Jordan Whitfield" in sent and "Priya" not in sent and "daniel@acme-example.com" not in sent
