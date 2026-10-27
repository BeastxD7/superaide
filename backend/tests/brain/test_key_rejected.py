"""A model key the provider refuses is permanent for the run: no endless retries, a visible verdict, a plain
message in chat, one in-app note per three hours, and recovery when a working key is saved (issue #150)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from superaide.brain import keyhealth, keynote
from superaide.brain.providers.base import Priced, metered
from superaide.brain.spend.budget import BudgetGuard
from superaide.brain.spend.pricing import UsageCounts
from superaide.jobs import queue
from superaide.jobs.queue import enqueue, run_due
from superaide.memory.knowledge.pending import brain_became_ready
from superaide.platform.config import get_settings
from superaide.platform.db.models import Document, Job
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_state
from superaide.vault.check import Checked
from tests.memory.knowledge.test_read_pending import FACTS, going_live, read_jobs, share, statuses, told  # noqa: F401
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

KEY = "sk-ant-api03-" + "x" * 40


class Rejected(Exception):
    """What the Anthropic SDK raises for a 401 (same shape: status_code and a body)."""

    status_code = 401

    def __init__(self) -> None:
        super().__init__("Error code: 401 - invalid x-api-key")
        self.body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}


@pytest.fixture
def provider(agent, monkeypatch):
    """The fake model: reading facts and chatting both fail with a 401 until `state['ok']` is set."""
    state = {"ok": False, "calls": 0}
    original = agent.llm.responder

    def responder(purpose, system, messages, output_model):
        state["calls"] += 1
        if not state["ok"]:
            raise Rejected()
        if purpose == "intro_facts":
            return FACTS
        return original(purpose, system, messages, output_model)

    agent.llm.responder = responder
    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: agent.llm)
    return state


def connect(client, monkeypatch) -> None:
    from superaide.app import connections

    monkeypatch.setattr(connections.check, "check_anthropic", lambda key, http: Checked(values={"api_key": key}))
    r = client.post("/app/providers/anthropic", json={"api_key": KEY})
    assert r.status_code == 200, r.text


def rejected_now() -> list[str]:
    with session_scope() as s:
        return keyhealth.rejected(s)


# --- classification ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "auth"),
    [
        (Rejected(), True),
        (type("AuthenticationError", (Exception,), {})("nope"), True),
        (type("PermissionDeniedError", (Exception,), {})("nope"), True),
        (type("E", (Exception,), {"status_code": 403})("forbidden"), True),
        (RuntimeError("Error code: 401 - invalid x-api-key"), True),
        (type("E", (Exception,), {"status_code": 429})("slow down"), False),
        (type("E", (Exception,), {"status_code": 500})("oops"), False),
        (ConnectionError("reset"), False),
        (RuntimeError("couldn't read every document just now"), False),
    ],
)
def test_auth_errors_are_told_from_passing_ones(exc, auth):
    assert keyhealth.is_auth_error(exc) is auth


# --- reading documents (the background job) ---------------------------------------------------


def test_a_rejected_key_stops_the_reading_job_and_tells_the_owner_once(owner_client, provider, monkeypatch):  # noqa: F811
    share(owner_client)
    going_live(monkeypatch)
    connect(owner_client, monkeypatch)
    assert run_due() >= 1  # the reading job (and the passages' embedding job)
    [job] = read_jobs()
    assert job.status == "done" and job.attempts == 1  # not queued for a retry with back-off
    assert statuses() == ["stored"]
    assert rejected_now() == ["anthropic"]
    notes = [t for t in told(owner_client) if "key was rejected" in t]
    assert len(notes) == 1 and "AI model page" in notes[0]
    assert KEY not in " ".join(told(owner_client))
    assert run_due() == 0
    assert provider["calls"] == 1  # one try, not a retry loop


def test_the_sweep_does_not_retry_a_rejected_key_but_a_new_key_resumes_reading(owner_client, provider, monkeypatch):  # noqa: F811
    from superaide.jobs.worker import JobWorker

    share(owner_client)
    going_live(monkeypatch)
    connect(owner_client, monkeypatch)
    run_due()
    calls = provider["calls"]
    assert JobWorker(sweep_every=1).sweep_if_due(now=10_000.0) is True
    assert run_due() == 0 and provider["calls"] == calls  # the sweep left a refused key alone

    provider["ok"] = True  # the owner saves a working key
    connect(owner_client, monkeypatch)
    assert rejected_now() == []
    assert run_due() >= 1
    assert statuses() == ["read"]
    assert any("I've read resume.txt" in t for t in told(owner_client))


def test_the_owner_hears_about_a_rejected_key_once_per_three_hours(owner_client, provider, monkeypatch):  # noqa: F811
    connect(owner_client, monkeypatch)
    from superaide.app.accounts import owner as hired_owner

    def note() -> bool:
        with session_scope() as s:
            person = hired_owner(s)
            assert person is not None
            return keynote.note_owner(s, person, "anthropic")

    assert note() is True and note() is False
    with session_scope() as s:
        from superaide.policy.killswitch import get_state

        at = datetime.fromisoformat(get_state(s, "ai_key_note")["at"])
        set_state(s, "ai_key_note", {"at": (at - timedelta(hours=3, minutes=1)).isoformat()})
    assert note() is True
    assert len([t for t in told(owner_client) if "key was rejected" in t]) == 2


def test_credits_get_their_own_wording(owner_client, provider, monkeypatch):  # noqa: F811
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        keynote.note_owner(s, person, "credits")
    assert any("Superaide's credits are unavailable right now" in t for t in told(owner_client))


# --- background jobs in general ---------------------------------------------------------------


def test_any_job_that_fails_with_a_rejected_key_is_not_retried(monkeypatch):
    monkeypatch.setattr(queue, "HANDLERS", {})

    @queue.handler("uses_model")
    def uses_model(session, job):
        raise Rejected()

    with session_scope() as s:
        enqueue(s, "uses_model", run_at=datetime.now(UTC) - timedelta(seconds=1), max_attempts=5)
    assert run_due() == 1
    with session_scope() as s:
        row = s.scalar(select(Job).where(Job.kind == "uses_model"))
        assert row is not None and row.status == "failed" and row.attempts == 1


# --- chat -------------------------------------------------------------------------------------


def test_chat_tells_the_owner_plainly_when_the_key_is_rejected(owner_client, provider):  # noqa: F811
    r = owner_client.post("/app/chat", json={"text": "What's on my plate today?"})
    assert r.status_code == 200, r.text
    assert "My AI key was rejected: check it on the AI model page." in r.text
    assert rejected_now() == ["anthropic"]
    assert KEY not in r.text


def test_chat_says_credits_are_unavailable_when_they_are_the_ones_refused(owner_client, agent):  # noqa: F811
    def responder(purpose, system, messages, output_model):
        err = Rejected()
        keyhealth.mark(err, "credits")
        raise err

    agent.llm.responder = responder
    r = owner_client.post("/app/chat", json={"text": "hello"})
    assert "Superaide's credits are unavailable right now." in r.text


def test_a_colleague_gets_a_plain_apology_and_the_owner_the_note(owner_client, provider, colleague, agent):  # noqa: F811
    from tests.conftest import sl

    result = agent.handle(sl("U_PRIYA", "Is Jordan around?"))
    assert result.reply == keyhealth.OTHERS_CHAT and "key" not in result.reply.lower()
    assert any("key was rejected" in t for t in told(owner_client))


# --- the metered path and the AI model page ------------------------------------------------------


def test_a_provider_refusal_in_a_metered_call_is_recorded_and_tagged(owner_client):  # noqa: F811
    guard = BudgetGuard(10)

    def refused() -> str:
        raise Rejected()

    with pytest.raises(Rejected) as info:
        metered(guard, purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.01"), call=refused,
                priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    assert info.value.key_rejected is True and info.value.key_provider == "anthropic"  # type: ignore[attr-defined]
    assert rejected_now() == ["anthropic"]


def test_platform_paid_calls_are_recorded_as_credits(owner_client):  # noqa: F811
    guard = BudgetGuard(10, key_source="platform")
    get_settings().platform_daily_cap_usd = 5.0
    try:
        with pytest.raises(Rejected):
            metered(guard, purpose="chat", model="claude-haiku-5-5", worst_case_usd=Decimal("0.01"),
                    call=lambda: (_ for _ in ()).throw(Rejected()), priced=lambda r: Priced(UsageCounts()))  # fmt: skip
    finally:
        get_settings().platform_daily_cap_usd = 0.0
    assert rejected_now() == ["credits"]


def test_the_ai_model_page_says_so_and_a_new_key_clears_it(owner_client, monkeypatch):  # noqa: F811
    assert owner_client.get("/app/providers").json()["key_status"] == {"rejected": [], "message": ""}
    keyhealth.record_rejected("anthropic")
    status = owner_client.get("/app/providers").json()["key_status"]
    assert status == {"rejected": ["anthropic"], "message": "Key rejected: reconnect"}
    connect(owner_client, monkeypatch)
    assert owner_client.get("/app/providers").json()["key_status"]["rejected"] == []


def test_the_verdict_never_holds_key_material(owner_client, provider, monkeypatch):  # noqa: F811
    connect(owner_client, monkeypatch)
    owner_client.post("/app/chat", json={"text": "hi"})
    with session_scope() as s:
        from superaide.platform.db.models import AgentState

        blob = " ".join(str(r.value) for r in s.scalars(select(AgentState)))
    assert KEY not in blob and "x" * 20 not in blob


def test_brain_became_ready_clears_the_verdict(owner_client):  # noqa: F811
    keyhealth.record_rejected("anthropic")
    brain_became_ready()
    assert rejected_now() == []
    assert Document is not None
