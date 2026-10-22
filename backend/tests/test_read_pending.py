"""Documents shared while no AI was connected are read as soon as the brain is ready, however it got ready
(issue #12): Claude connect, a provider, a chosen model, Superaide AI credits, the operator's key, a restart,
or the periodic sweep when nothing else noticed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.brain.providers.base import CheckReport
from superaide.config import get_settings
from superaide.db.models import Document, Job, MemoryItem
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.facts import Fact, FactList
from superaide.vault.check import Checked
from tests.test_loop import owner_client  # noqa: F401  (fixture)

load()

KEY = "sk-or-test-key-000000000000"
PLATFORM_KEY = "sk-ant-platform-secret-key-000000000000"
FACTS = FactList(
    facts=[
        Fact(content="Shashank leads applied AI at PebbleRoad.", category="work", confidence=1),
        Fact(content="Shashank is based in Singapore.", category="about", confidence=0.9),
    ]
)


@pytest.fixture
def reads(agent, monkeypatch):
    """The fake model answers fact-extraction calls (and records them); reading uses it."""
    calls: list[str] = []
    original = agent.llm.responder

    def responder(purpose, system, messages, output_model):
        if purpose == "intro_facts":
            calls.append(messages[0]["content"])
            if getattr(responder, "fail", False):
                raise RuntimeError("answer cut off")
            return responder.result
        return original(purpose, system, messages, output_model)

    agent.llm.responder = responder
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: agent.llm)
    responder.calls = calls  # type: ignore[attr-defined]
    responder.result = FACTS  # type: ignore[attr-defined]
    return responder


def share(client, name: str = "resume.txt") -> None:
    """Share a file while no AI is connected: it is kept, not read."""
    r = client.post(
        "/app/intro/files",
        files={"file": (name, b"Shashank Rao\nApplied AI lead, PebbleRoad, Singapore", "application/octet-stream")},
    )
    assert r.status_code == 200 and r.json()["status"] == "stored", r.text


def going_live(monkeypatch) -> None:
    """The server is no longer forced offline; what makes the brain ready is the owner's own choice."""
    monkeypatch.setattr(get_settings(), "fake_llm", False)


def statuses() -> list[str]:
    with session_scope() as s:
        return [d.status for d in s.scalars(select(Document))]


def facts_saved() -> int:
    with session_scope() as s:
        return len(s.scalars(select(MemoryItem).where(MemoryItem.document_id.is_not(None))).all())


def read_jobs() -> list[Job]:
    with session_scope() as s:
        rows = list(s.scalars(select(Job).where(Job.kind == "read_pending")))
        s.expunge_all()
        return rows


def told(client) -> list[str]:
    return [m["text"] for m in client.get("/app/messages").json() if m["from"] == "agent"]


def assert_read_and_told(client, name: str = "resume.txt") -> None:
    assert run_due() >= 1
    assert statuses() == ["read"] and facts_saved() == 2
    assert f"I've read {name}: 2 facts saved." in told(client)


def test_connecting_claude_in_connections_reads_the_waiting_documents(owner_client, reads, monkeypatch):  # noqa: F811
    from superaide.app import connections

    monkeypatch.setattr(connections.check, "check_anthropic", lambda key, http: Checked(values={"api_key": key}))
    share(owner_client)
    going_live(monkeypatch)
    r = owner_client.post("/app/connections/anthropic", json={"api_key": "sk-ant-api03-" + "x" * 40})
    assert r.status_code == 200, r.text
    assert_read_and_told(owner_client)


def test_connecting_claude_on_the_ai_model_page_reads_the_waiting_documents(owner_client, reads, monkeypatch):  # noqa: F811
    from superaide.app import providers

    monkeypatch.setattr(
        providers.check, "check_anthropic", lambda key, http: Checked(values={"api_key": key}), raising=True
    )
    share(owner_client)
    going_live(monkeypatch)
    r = owner_client.post("/app/providers/anthropic", json={"api_key": "sk-ant-api03-" + "x" * 40})
    assert r.status_code == 200, r.text
    assert_read_and_told(owner_client)


def test_choosing_a_connected_provider_for_chat_reads_the_waiting_documents(owner_client, reads, monkeypatch):  # noqa: F811
    listed = {"acme/fast": {"input": "1.000000", "output": "4.000000"}}

    class Service:
        def __init__(self, preset, api_key, base_url):
            pass

        def check(self):
            return CheckReport(True, "Connected", ["acme/fast"], listed)

    monkeypatch.setattr("superaide.app.providers.build_provider", Service)
    share(owner_client)
    going_live(monkeypatch)
    assert owner_client.post("/app/providers/openrouter", json={"api_key": KEY}).status_code == 200
    # Connected, but chat still runs on Claude, which has no key: not ready, nothing to read with.
    assert read_jobs() == [] and statuses() == ["stored"]
    choice = {"chat": {"provider": "openrouter", "model": "acme/fast"}}
    assert owner_client.put("/app/providers/roles", json=choice).status_code == 200
    assert_read_and_told(owner_client)


@pytest.fixture
def credits(monkeypatch):
    env = get_settings()
    monkeypatch.setattr(env, "deploy_mode", "hosted")
    monkeypatch.setattr(env, "platform_ai_enabled", True)
    monkeypatch.setattr(env, "platform_anthropic_api_key", PLATFORM_KEY)
    return env


def test_opting_in_to_superaide_ai_credits_reads_the_waiting_documents(owner_client, reads, credits, monkeypatch):  # noqa: F811
    share(owner_client)
    going_live(monkeypatch)
    r = owner_client.post("/app/providers/credits", json={"on": True})
    assert r.status_code == 200 and r.json()["credits"]["opted_in"] is True
    assert_read_and_told(owner_client)


def test_opting_out_of_credits_reads_nothing(owner_client, reads, credits, monkeypatch):  # noqa: F811
    share(owner_client)
    going_live(monkeypatch)
    assert owner_client.post("/app/providers/credits", json={"on": False}).status_code == 200
    assert read_jobs() == [] and run_due() == 0 and statuses() == ["stored"] and reads.calls == []


def test_the_operators_own_key_is_picked_up_on_the_next_start(owner_client, reads, credits, monkeypatch):  # noqa: F811
    from superaide.jobs.worker import schedule_recurring

    share(owner_client)  # shared before the operator's email was listed: offline, so kept unread
    going_live(monkeypatch)
    monkeypatch.setattr(credits, "anthropic_api_key", PLATFORM_KEY)
    monkeypatch.setattr(credits, "platform_key_owner_emails", "s@example.com")  # then listed, and the server restarted
    schedule_recurring()  # what the worker does when it starts
    assert_read_and_told(owner_client)


def test_the_periodic_sweep_reads_documents_nobody_else_noticed(owner_client, reads, credits, monkeypatch):  # noqa: F811
    from superaide.jobs.worker import JobWorker

    share(owner_client)
    going_live(monkeypatch)
    monkeypatch.setattr(credits, "anthropic_api_key", PLATFORM_KEY)
    monkeypatch.setattr(credits, "platform_key_owner_emails", "s@example.com")
    worker = JobWorker(sweep_every=600)
    assert worker.sweep_if_due(now=1000.0) is True  # first tick: sweeps
    assert worker.sweep_if_due(now=1100.0) is False  # not again until the interval has passed
    assert_read_and_told(owner_client)
    with session_scope() as s:  # another one waiting (shared when the brain was off), nobody asked for it to be read
        s.scalar(select(Document)).status = "stored"
    assert worker.sweep_if_due(now=1601.0) is True
    assert len(read_jobs()) == 1  # the finished job is replaced, never piled up


def test_without_a_ready_brain_nothing_is_scheduled(owner_client, reads, monkeypatch):  # noqa: F811
    from superaide.knowledge import pending

    share(owner_client)
    assert pending.brain_became_ready() is False
    assert pending.sweep() == 0
    assert read_jobs() == [] and statuses() == ["stored"] and reads.calls == []


def test_asking_twice_schedules_one_job_and_reads_each_document_once(owner_client, reads, credits, monkeypatch):  # noqa: F811
    from superaide.knowledge import pending

    share(owner_client)
    going_live(monkeypatch)
    assert owner_client.post("/app/providers/credits", json={"on": True}).status_code == 200
    pending.brain_became_ready()
    pending.sweep()
    assert len(read_jobs()) == 1
    assert_read_and_told(owner_client)
    assert len(reads.calls) == 1
    assert pending.brain_became_ready() is False  # nothing waiting any more
    run_due()
    assert len(reads.calls) == 1


def test_a_read_that_fails_is_retried_and_the_owner_is_not_told_it_was_read(owner_client, reads, credits, monkeypatch):  # noqa: F811
    share(owner_client)
    going_live(monkeypatch)
    reads.fail = True
    assert owner_client.post("/app/providers/credits", json={"on": True}).status_code == 200
    assert run_due() == 1
    assert statuses() == ["stored"] and not any("I've read" in t for t in told(owner_client))
    [job] = read_jobs()
    assert job.status == "queued" and job.attempts == 1 and job.last_error  # retried later, with back-off
    reads.fail = False
    from datetime import UTC, datetime, timedelta

    assert run_due(datetime.now(UTC) + timedelta(hours=2)) == 1
    assert statuses() == ["read"]
    assert told(owner_client).count("I've read resume.txt: 2 facts saved.") == 1


def test_the_note_that_a_document_was_read_goes_only_to_the_owner(owner_client, reads, credits, monkeypatch):  # noqa: F811
    from superaide.db.models import Event

    share(owner_client)
    going_live(monkeypatch)
    owner_client.post("/app/providers/credits", json={"on": True})
    run_due()
    with session_scope() as s:
        event = s.scalar(select(Event).where(Event.content.like("I've read%")))
        assert event is not None and event.direction == "out"
        assert len(event.audience_person_ids) == 1 and not event.audience_has_unknown


def test_a_document_that_found_nothing_says_so(owner_client, reads, credits, monkeypatch):  # noqa: F811
    reads.result = FactList(facts=[])
    share(owner_client)
    going_live(monkeypatch)
    owner_client.post("/app/providers/credits", json={"on": True})
    assert run_due() >= 1
    assert statuses() == ["read"] and facts_saved() == 0
    assert "I've read resume.txt, but didn't find anything new about you in it." in told(owner_client)
