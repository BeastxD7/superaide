# ruff: noqa: F811
"""#326: an approval is created only when something can carry it out, and every kind says what yes does.

The Bluehost shape: the owner asked Ava to cancel a subscription "via their contact page"; she queued a
message_someone_else approval, and approving it recorded "Nothing can carry this out yet" while she had told
the owner it was queued to be sent."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Approval
from superaide.db.session import session_scope
from superaide.policy import kinds
from superaide.policy.actions import CREATION_RULES, EXECUTORS, RULES, TOOL_RULES, _load_executors, resolve
from superaide.policy.binding import seal
from tests.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401, F811  (fixtures)


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_require_executor", True)


def test_every_approval_kind_has_a_description_and_an_executor_or_an_explicit_marker():
    _load_executors()
    every = set(RULES) | set(CREATION_RULES) | set(TOOL_RULES) | {"gmail_send", "ask_reply"} | set(EXECUTORS)
    for kind in sorted(every):
        info = kinds.KINDS.get(kind)
        assert info is not None, f"{kind} has no entry in policy/kinds.py"
        assert info.label and info.on_approval.strip().endswith("."), kind
        # the declaration must match reality, both ways
        assert info.executes == (kind in EXECUTORS), (
            f"{kind}: executes={info.executes} but executor registered={kind in EXECUTORS}"
        )
        if not info.executes:
            assert info.on_approval.startswith("Nothing"), f"{kind} has no executor and must say so"
    assert set(kinds.KINDS) <= every


def test_a_contact_page_request_creates_no_approval_and_the_refusal_says_what_ava_can_do(owner_client, strict):
    with session_scope() as s:
        script = Script(
            tool_use(
                "propose_action",
                {
                    "kind": "message_someone_else",
                    "summary": "Cancel the Bluehost subscription via their contact page",
                    "rationale": "Owner asked",
                },
            ),
            "I can't use their contact page. Here is the text to paste.",
        )
        result = turn_for(s, script, "cancel bluehost via their contact page").run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
        refused = script.seen[1][-1]["content"][0]
        assert refused["is_error"] is True
        assert "NO approval was created" in refused["content"]
        assert "contact page" in refused["content"]
        assert "gmail_draft_reply" in refused["content"] and "copy and send themselves" in refused["content"]
    assert "action:message_someone_else:unsupported" in result.notes
    assert "queued" not in result.text.lower()


def test_with_the_flag_off_the_old_behaviour_is_kept(owner_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "approval_require_executor", False)
    with session_scope() as s:
        script = Script(tool_use("propose_action", {"kind": "publish_post", "summary": "x", "rationale": "y"}), "ok")
        turn_for(s, script).run([{"role": "user", "content": "x"}])
        assert len(s.scalars(select(Approval)).all()) == 1  # flag off: the old behaviour


def test_the_tool_description_tells_the_model_what_each_kind_does():
    from superaide.brain.loop import ACTION_TOOL

    d = ACTION_TOOL["description"]
    assert "NO approval" in d or "no approval is created" in d
    assert kinds.on_approval("message_someone_else") in d


def test_approving_a_card_with_no_executor_says_it_did_nothing(owner_client):
    with session_scope() as s:
        script = Script(
            tool_use(
                "propose_action",
                {"kind": "message_someone_else", "summary": "x", "rationale": "y", "reaches_a_person": "yes"},
            ),
            "ok",
        )
        turn_for(s, script).run([{"role": "user", "content": "x"}])
    got = owner_client.get("/app/approvals").json()
    [card] = got
    assert card["executes"] is False and card["on_approval"].startswith("Nothing")
    done = owner_client.post(f"/app/approvals/{card['id']}/approve").json()
    assert "will not do anything" in done["result"]


def test_the_card_api_marks_kinds_that_do_something(owner_client):
    with session_scope() as s:
        s.add(Approval(kind="gmail_send", payload={"summary": "Email Daniel"}))
    [card] = owner_client.get("/app/approvals").json()
    assert card["executes"] is True and "Sends the saved Gmail draft" in card["on_approval"]


def test_resolve_of_an_unexecutable_kind_does_nothing(owner_client):
    with session_scope() as s:
        a = Approval(kind="delete_memory", payload={"summary": "x"})
        seal(a)
        s.add(a)
        s.flush()
        resolve(s, a, approve=True, decided_by="Owner")
        assert "will not do anything" in a.payload["result"]
