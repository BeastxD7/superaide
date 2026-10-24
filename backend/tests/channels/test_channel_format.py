"""Ava's Markdown on Telegram and Slack: conversion, splitting, the sources footer. No real network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from superaide.channels import format as fmt
from superaide.channels.slack import SlackMessenger
from superaide.channels.telegram import TelegramMessenger
from superaide.platform.db.models import ChannelIdentity, Context, Event, MemoryItem
from superaide.platform.db.session import session_scope

TOKEN = "123456789:" + "A" * 35
WEB = {"n": 2, "uri": "https://www.example.com/a?b=1", "kind": "web", "title": "Example", "cited_text": "SECRET"}
NOTE = {"n": 1, "uri": "ava://memory/1", "kind": "memory", "title": "Note · preference", "cited_text": "PRIVATE"}


# --- Telegram HTML --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "html"),
    [
        ("You dislike **black**.", "You dislike <b>black</b>."),
        ("a *soft* word and _this_ too", "a <i>soft</i> word and <i>this</i> too"),
        ("run `ls -la` now", "run <code>ls -la</code> now"),
        ("```py\nx = 1 < 2\n\n**not bold**\n```", "<pre>x = 1 &lt; 2\n\n**not bold**</pre>"),
        ("see [the docs](https://x.com/a?q=1&r=2)", 'see <a href="https://x.com/a?q=1&amp;r=2">the docs</a>'),
        ("- one\n- two\n* three", "• one\n• two\n• three"),
        ("## Plan", "<b>Plan</b>"),
        ("~~gone~~", "<s>gone</s>"),
    ],
)
def test_markdown_becomes_telegram_html(md, html):
    assert fmt.to_telegram_html(md) == html


@pytest.mark.parametrize(
    ("md", "html"),
    [
        ("**unclosed bold", "**unclosed bold"),
        ("2 * 3 * 4 = 24", "2 * 3 * 4 = 24"),
        ("a lone ` backtick", "a lone ` backtick"),
        ("snake_case_name stays", "snake_case_name stays"),
        ("<b>hi</b> & <script>x()</script>", "&lt;b&gt;hi&lt;/b&gt; &amp; &lt;script&gt;x()&lt;/script&gt;"),
        ("Héllo **мир** 🎉 *日本語*", "Héllo <b>мир</b> 🎉 <i>日本語</i>"),
        ("[click](javascript:alert(1))", "[click](javascript:alert(1))"),
    ],
)
def test_tricky_input_stays_literal_and_safe(md, html):
    assert fmt.to_telegram_html(md) == html


@pytest.mark.parametrize("md", ["**a *b** c*", "*a **b* c**", "**x** *y* **z*", "__a **b__ c**", "`**`**"])
def test_overlapping_marks_never_make_unbalanced_tags(md):
    assert fmt._balanced(fmt.to_telegram_html(md))


# --- Slack mrkdwn ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "mrkdwn"),
    [
        ("You dislike **black**.", "You dislike *black*."),
        ("a *soft* word", "a _soft_ word"),
        ("[docs](https://x.com/p)", "<https://x.com/p|docs>"),
        ("1 < 2 & 3 > 2", "1 &lt; 2 &amp; 3 &gt; 2"),
        ("# Title\n- item", "*Title*\n• item"),
        ("~~old~~ `a*b*c`", "~old~ `a*b*c`"),
    ],
)
def test_markdown_becomes_slack_mrkdwn(md, mrkdwn):
    assert fmt.to_slack(md) == mrkdwn


# --- citations ------------------------------------------------------------------------


def test_citation_marks_are_stripped_but_indexing_and_links_survive():
    assert fmt.strip_citations("Thursday [1]. Also [2][3], items[1] and [x](https://a.b)") == (
        "Thursday. Also, items[1] and [x](https://a.b)"
    )


def test_footer_shows_titles_and_web_domains_never_the_cited_text():
    tg = fmt.telegram_footer([NOTE, WEB])
    assert tg == (
        '<i>Sources: [1] Note · preference · [2] <a href="https://www.example.com/a?b=1">example.com</a>\n'
        f"{fmt.SOURCE_NOTE}</i>"
    )
    assert fmt.slack_footer([NOTE, WEB]) == (
        f"_Sources: [1] Note · preference · [2] <https://www.example.com/a?b=1|example.com>\n{fmt.SOURCE_NOTE}_"
    )
    for footer in (tg, fmt.slack_footer([NOTE, WEB]), fmt.plain_footer([NOTE, WEB])):
        assert "SECRET" not in footer and "PRIVATE" not in footer


def test_a_title_with_markup_is_escaped_not_rendered():
    footer = fmt.telegram_footer([{"n": 1, "uri": "ava://msg/1", "kind": "message", "title": "<b>*hi*</b>"}])
    assert "&lt;b&gt;*hi*&lt;/b&gt;" in footer and fmt._balanced(footer)


# --- splitting ------------------------------------------------------------------------


def test_short_text_is_one_message():
    assert fmt.split_markdown("hello", 4096) == ["hello"]


def test_long_text_splits_on_paragraphs_under_the_limit():
    paras = [f"Paragraph {i} " + "word " * 150 for i in range(12)]
    chunks = fmt.split_markdown("\n\n".join(paras), 4096)
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert "\n\n".join(chunks).split("\n\n") == paras  # nothing lost, broken only between paragraphs


def test_a_huge_code_block_is_refenced_in_each_piece():
    code = "```\n" + "\n".join(f"line {i} < {i + 1}" for i in range(600)) + "\n```"
    chunks = fmt.split_markdown(code, 4096, lambda t: len(fmt.to_telegram_html(t)))
    assert len(chunks) > 1
    for c in chunks:
        html = fmt.to_telegram_html(c)
        assert html.startswith("<pre>") and html.endswith("</pre>") and len(html) <= 4096


def test_one_enormous_line_is_cut_by_characters():
    chunks = fmt.split_markdown("é" * 10_000, 4096)
    assert all(len(c) <= 4096 for c in chunks) and "".join(chunks) == "é" * 10_000


# --- Telegram messenger ---------------------------------------------------------------


def telegram(*statuses: tuple[int, dict[str, Any]]) -> tuple[TelegramMessenger, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    replies = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        status, body = replies.pop(0) if replies else (200, {"ok": True})
        return httpx.Response(status, json=body)

    return TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(handler))), sent


def test_telegram_sends_html_with_parse_mode():
    messenger, sent = telegram()
    messenger.send_dm(ChannelIdentity(stable_key="1001"), "You dislike **black** <3")
    [body] = sent
    assert body["parse_mode"] == "HTML"
    assert body["text"] == "You dislike <b>black</b> &lt;3"
    assert body["link_preview_options"] == {"is_disabled": True}


def test_telegram_falls_back_to_plain_text_when_it_cannot_parse_the_markup():
    messenger, sent = telegram(
        (400, {"ok": False, "description": "Bad Request: can't parse entities: unexpected end tag"})
    )
    messenger.send_dm(ChannelIdentity(stable_key="1001"), "You dislike **black**. [docs](https://x.com)")
    first, second = sent
    assert first["parse_mode"] == "HTML"
    assert "parse_mode" not in second
    assert second["text"] == "You dislike black. docs (https://x.com)"


def test_other_telegram_failures_are_not_retried():
    messenger, sent = telegram((403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}))
    messenger.send_dm(ChannelIdentity(stable_key="1001"), "hi")
    assert len(sent) == 1


def test_a_long_telegram_reply_goes_as_several_messages_in_the_same_topic():
    messenger, sent = telegram()
    text = "\n\n".join(f"**Part {i}** " + "word " * 300 for i in range(6))
    messenger.send_to_context(Context(external_id="-100"), text, "12")
    assert len(sent) > 1
    assert all(len(b["text"]) <= 4096 and b["message_thread_id"] == 12 for b in sent)
    assert all(fmt._balanced(b["text"]) for b in sent)


def test_telegram_appends_the_sources_footer_when_given():
    messenger, sent = telegram()
    messenger.send_to_context(Context(external_id="1001"), "Thursday [1], per [2].", sources=[NOTE, WEB])
    [body] = sent
    assert body["text"].startswith("Thursday [1], per [2].\n\n<i>Sources: [1] Note · preference")
    assert '<a href="https://www.example.com/a?b=1">example.com</a>' in body["text"]
    assert "PRIVATE" not in body["text"] and "SECRET" not in body["text"]


# --- Slack messenger ------------------------------------------------------------------


class FakeWebClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "channel": {"id": "D1"}}

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - slack_sdk's name
        self.posts.append(kwargs)
        return {"ok": True}


def test_slack_sends_mrkdwn_and_the_footer():
    client = FakeWebClient()
    SlackMessenger("xoxb-test", client).send_to_context(
        Context(external_id="D1"), "You dislike **black** [1].", "17.1", sources=[WEB]
    )
    [post] = client.posts
    assert post["thread_ts"] == "17.1"
    assert post["text"] == (
        f"You dislike *black* [1].\n\n_Sources: [2] <https://www.example.com/a?b=1|example.com>\n{fmt.SOURCE_NOTE}_"
    )


# --- the pipeline: footer only in the owner's own DM -----------------------------------


@pytest.fixture
def owner_on_telegram(agent, monkeypatch):
    from fastapi.testclient import TestClient

    from superaide.app.accounts import owner as hired_owner
    from superaide.identity.trust import Trust
    from superaide.main import create_app

    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: False)
    agent.settings.web_tools_enabled = False
    app = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    app.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": "correct horse 1"})
    app.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        me = hired_owner(s)
        assert me is not None
        s.add(ChannelIdentity(person_id=me.id, provider="telegram", tenant_scope="", stable_key="777001",
                              trust_level=int(Trust.LINKED), verification={"method": "test"}))  # fmt: skip
        m = MemoryItem(
            content="Dislikes the colour black", subject_person_ids=[me.id], allowed_kind="persons",
            allowed_person_ids=[me.id], min_trust=2,
        )  # fmt: skip
        s.add(m)
        s.flush()
        return f"ava://memory/{m.id}"


def cited_reply(uri: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": "You dislike **black**.",
            "citations": [
                {
                    "type": "search_result_location",
                    "source": uri,
                    "title": "Note · preference",
                    "cited_text": "Dislikes the colour black",
                }
            ],
        }
    ]


def test_owner_dm_on_telegram_gets_the_footer_and_the_event_keeps_markdown(agent, outbox, owner_on_telegram):
    from tests.conftest import tg

    steps: list[Any] = [
        [{"type": "tool_use", "id": "t1", "name": "recall", "input": {"query": "black"}}],
        cited_reply(owner_on_telegram),
    ]
    agent.llm.responder = lambda purpose, system, messages, output_model: steps.pop(0)
    agent.handle(tg("777001", "Do I like black?"))
    [sent] = outbox["telegram"].sent
    assert sent["text"] == "You dislike **black**. [1]"
    assert sent["sources"] == [
        {"n": 1, "uri": owner_on_telegram, "kind": "memory", "title": "Note · preference",
         "cited_text": "Dislikes the colour black"}
    ]  # fmt: skip
    with session_scope() as s:
        out = s.scalars(select(Event).where(Event.direction == "out")).all()[-1]
        assert out.content == "You dislike **black**. [1]" and out.meta["sources"]


def test_citation_marks_are_stripped_outside_the_owner_dm(agent, outbox, colleague, model):
    from tests.conftest import COLLEAGUE_SLACK, sl

    model.reply = "Yes, Thursday [1]."
    agent.handle(sl(COLLEAGUE_SLACK, "is the meeting on?"))
    [sent] = outbox["slack"].sent
    assert sent["text"] == "Yes, Thursday." and "sources" not in sent


# --- deep links in the footer (#341 C) --------------------------------------------------

MEM_ID = "3f2b8c1e-aaaa-bbbb-cccc-0123456789ab"


def test_a_note_and_an_email_get_deep_links_built_from_the_stored_id(monkeypatch):
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "public_base_url", "https://ava.example.org/")
    note = {"n": 1, "uri": f"ava://memory/{MEM_ID}", "kind": "memory", "title": "Note · about", "cited_text": "SECRET"}
    mail = {"n": 2, "uri": "ava://mail/18c0ffee12ab", "kind": "email", "title": "Squares", "cited_text": "PRIVATE"}
    tg = fmt.telegram_footer([note, mail])
    assert f'<a href="https://ava.example.org/dashboard/notebook#note-{MEM_ID}">Note · about</a>' in tg
    assert '<a href="https://mail.google.com/mail/u/0/#all/18c0ffee12ab">Squares</a>' in tg
    assert f"<https://ava.example.org/dashboard/notebook#note-{MEM_ID}|Note · about>" in fmt.slack_footer([note, mail])
    assert fmt.SOURCE_NOTE in tg and fmt.SOURCE_NOTE in fmt.plain_footer([note])
    for footer in (tg, fmt.slack_footer([note, mail]), fmt.plain_footer([note, mail])):
        assert "SECRET" not in footer and "PRIVATE" not in footer


def test_a_source_id_that_is_not_ours_gets_no_link_and_titles_never_become_links():
    evil = [
        {"n": 1, "uri": "ava://memory/../../x", "kind": "memory", "title": "https://evil.example"},
        {"n": 2, "uri": "ava://mail/a/../b", "kind": "email", "title": "x"},
        {"n": 3, "uri": "javascript:alert(1)", "kind": "web", "title": "x"},
        {"n": 4, "uri": f"ava://chunk/{MEM_ID}", "kind": "memory", "title": "File"},
        {"n": 5, "uri": "ava://msg/telegram/1", "kind": "message", "title": "Chat"},
    ]
    assert [e[2] for e in fmt.source_entries(evil)] == [""] * 5
    assert "<a " not in fmt.telegram_footer(evil)


def test_no_sources_no_footer():
    assert fmt.telegram_footer([]) == fmt.slack_footer([]) == fmt.plain_footer([]) == ""
