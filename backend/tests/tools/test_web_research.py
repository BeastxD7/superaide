"""Web research limits and audit, and search over the owner's own files (#671)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from superaide import doctor
from superaide.app.accounts import owner as hired_owner
from superaide.brain.loop import OwnerTurn
from superaide.memory.audience import Audience
from superaide.memory.knowledge.ingest import store_chunks
from superaide.memory.models import Document
from superaide.memory.recall.retrieval import search_files
from superaide.platform.audit import AuditLog
from superaide.platform.config import Settings
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixture)

PAGE = "https://93.184.216.34/page"


class FakeWeb:
    """A stand-in for the real network: records calls, never touches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run_substitute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        self.calls.append((name, args))
        src = {"type": "search_result", "source": PAGE, "title": "Page", "content": [{"type": "text", "text": "Warm."}]}
        return [src], False


def _audits(action: str) -> list[AuditLog]:
    with session_scope() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


def _web_turn(s, script: Script, web: FakeWeb) -> OwnerTurn:
    turn = turn_for(s, script, web=True)
    turn.llm.run_substitute = web.run_substitute  # type: ignore[attr-defined]
    turn.tools = lambda: [{"name": "web_fetch", "description": "x", "input_schema": {"type": "object"}}]  # type: ignore[method-assign]
    return turn


@pytest.mark.usefixtures("owner_client")
def test_a_web_read_is_audited_with_the_host_and_never_the_query():
    web = FakeWeb()
    with session_scope() as s:
        script = Script(tool_use("web_fetch", {"url": PAGE}), "Warm.")
        result = _web_turn(s, script, web).run([{"role": "user", "content": "weather?"}])
    assert result.tainted
    [row] = _audits("web_read")
    assert row.detail["host"] == "93.184.216.34" and row.detail["results"] == 1 and "query" not in row.detail


@pytest.mark.usefixtures("owner_client")
def test_pages_past_the_owners_per_step_limit_are_refused_before_any_request():
    web = FakeWeb()
    with session_scope() as s:
        steps = [tool_use("web_fetch", {"url": PAGE}, f"t{i}") for i in range(4)] + ["Done."]
        script = Script(*steps)
        _web_turn(s, script, web).run([{"role": "user", "content": "read them"}])
    assert len(web.calls) == 3  # the default: 3 pages per step
    refused = script.seen[-1][-1]["content"][0]["content"]
    assert refused.startswith("Stopped: web_fetch reached its limit of 3")
    assert _audits("limit_reached")


def _share(s, owner_id, title: str, text: str) -> None:
    doc = Document(owner_person_id=owner_id, kind="text", title=title, source=title, sha256=title, text=text)
    s.add(doc)
    s.flush()
    store_chunks(s, doc)


@pytest.mark.usefixtures("owner_client")
def test_file_search_finds_the_owners_passages_by_file_and_nobody_elses():
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        _share(s, owner.id, "Harbour lease", "The harbour office lease ends on 30 June and renews for two years.")
        _share(s, owner.id, "Travel plan", "The conference in Pune is in March; hotel booked near the harbour.")
        alone = Audience(person_ids={owner.id}, min_trust=3)
        both = search_files(s, alone, "harbour")
        only = search_files(s, alone, "harbour", file="lease")
        assert len(both) == 2 and [x.title for x in only] == ["Passage 1 · Harbour lease"]
        # A visitor's audience, an unknown person present, or a different person: nothing
        assert search_files(s, Audience(person_ids={owner.id}, has_unknown=True, min_trust=3), "harbour") == []
        assert search_files(s, Audience(person_ids={owner.id}, min_trust=1), "harbour") == []
        assert search_files(s, Audience(person_ids={owner.id, owner.id.__class__(int=1)}, min_trust=3), "harbour") == []


@pytest.mark.usefixtures("owner_client")
def test_the_search_files_tool_cites_a_passage_and_says_so_when_nothing_matches():
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        _share(s, owner.id, "Harbour lease", "The harbour office lease ends on 30 June.")
        script = Script(
            tool_use("search_files", {"query": "lease"}), tool_use("search_files", {"query": "zzqx"}, "t2"), "ok"
        )
        result = turn_for(s, script).run([{"role": "user", "content": "when does my lease end?"}])
    block = script.seen[1][-1]["content"][0]["content"][0]
    assert block["type"] == "search_result" and block["source"].startswith("ava://chunk/") and "30 June" in str(block)
    assert "search_files:1" in result.notes
    assert script.seen[2][-1]["content"][0]["content"].startswith("Nothing in the owner's shared files")
    assert _audits("search_files")


@pytest.mark.usefixtures("owner_client")
def test_a_channel_turn_cannot_use_file_search():
    with session_scope() as s:
        script = Script(tool_use("search_files", {"query": "lease"}), "ok")
        turn = turn_for(s, script)
        turn.shared = True
        turn.run([{"role": "user", "content": "x"}])
    assert "can't" in str(script.seen[1][-1]["content"][0]["content"]).lower() or _audits("tool_refused_in_channel")


def test_check_my_setup_says_how_to_get_web_search_and_never_shows_the_key():
    off = [r for r in doctor.run_in_app(Settings(_env_file=None)) if r.name == "Web research"]
    on = [
        r
        for r in doctor.run_in_app(Settings(_env_file=None, brave_search_api_key="BSA-secret-123456"))
        if r.name == "Web research"
    ]
    assert off and "Brave" in off[0].fix and on and on[0].status == doctor.OK
    assert "secret" not in repr(on)
