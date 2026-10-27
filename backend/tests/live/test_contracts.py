"""Live contract tests: the real Claude API through the real code paths.

The fake model can't see what the real one does with max_tokens (it thinks
before answering, and the thinking counts), so these run the actual calls and
check that each answer finished and parsed. They spend real money (about
US$0.01 for the whole file) and are skipped unless you ask for them:

    cd backend && RUN_LIVE=1 uv run pytest -q tests/live

The key is read from ANTHROPIC_API_KEY in backend/.env. Spend is recorded in the
test database's ledger (TEST_DATABASE_URL), not in your real one.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.brain.llm import ClaudeLLM, LLMResult
from superaide.brain.spend.budget import BudgetGuard
from superaide.brain.turn import TurnOutput
from superaide.main import create_app
from superaide.memory.knowledge.facts import FactList, extract_facts
from superaide.memory.models import MemoryItem
from superaide.pipeline import Agent
from superaide.platform.config import Settings
from superaide.platform.db.session import session_scope
from tests.conftest import MANAGER_TG, tg

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
RESUME = Path(__file__).with_name("fixtures") / "resume.txt"
SUITE_LIMIT_USD = Decimal("0.02")  # the whole file must cost less than this
PER_TEST_CAP_USD = 0.25  # ledger cap per test: a runaway guard; a worst-case Sonnet chat reservation is ~US$0.09


class _EnvFileOnly(Settings):
    """Settings read from backend/.env alone. tests/conftest.py blanks ANTHROPIC_API_KEY in the
    environment, and environment variables would otherwise win over the file."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings, dotenv_settings)


def _api_key() -> str:
    if not ENV_FILE.exists():
        return ""
    try:
        return _EnvFileOnly(_env_file=ENV_FILE).anthropic_api_key.strip()
    except Exception:
        return ""


def _live_ready() -> bool:
    return os.environ.get("RUN_LIVE") == "1" and _api_key().startswith("sk-ant-")


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _live_ready(), reason="live: set RUN_LIVE=1 and ANTHROPIC_API_KEY in backend/.env"),
]

SPENT: list[tuple[str, str, int, int, Decimal]] = []  # purpose, model, output tokens, max_tokens, cost


class RecordingLLM:
    """The real ClaudeLLM, keeping every result: the code under test only returns what it parsed."""

    def __init__(self, inner: ClaudeLLM) -> None:
        self.inner = inner
        self.results: list[tuple[dict[str, Any], LLMResult]] = []

    def complete(self, **kwargs: Any) -> LLMResult:
        result = self.inner.complete(**kwargs)
        self.results.append((kwargs, result))
        SPENT.append(
            (kwargs["purpose"], result.model, result.usage.output_tokens, kwargs["max_tokens"], result.cost_usd)
        )
        return result


def _spent() -> Decimal:
    return sum((cost for *_, cost in SPENT), Decimal(0))


@pytest.fixture(scope="module", autouse=True)
def _report_cost(request):
    yield
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    lines = [f"  {p}: {m}, {out}/{cap} output tokens, US${c:.6f}" for p, m, out, cap, c in SPENT]
    summary = f"live contract tests: {len(SPENT)} Claude calls, settled cost US${_spent():.6f}"
    for line in ["", summary, *lines]:
        if reporter is not None:
            reporter.write_line(line)
        else:
            print(line)


@pytest.fixture(autouse=True)
def _within_suite_budget():
    if _spent() >= SUITE_LIMIT_USD:
        pytest.skip(f"live suite already spent US${_spent():.4f} of its US${SUITE_LIMIT_USD} allowance")
    yield


@pytest.fixture
def live_settings(settings) -> Settings:
    return settings.model_copy(update={"anthropic_api_key": _api_key(), "fake_llm": False})


@pytest.fixture
def llm(live_settings) -> RecordingLLM:
    guard = BudgetGuard(min(live_settings.max_spend_usd, PER_TEST_CAP_USD))
    return RecordingLLM(ClaudeLLM(guard, api_key=live_settings.anthropic_api_key))


@pytest.fixture
def live_agent(llm, outbox, live_settings) -> Agent:
    return Agent(llm=llm, messengers=outbox, settings=live_settings)


def assert_finished_and_parsed(llm: RecordingLLM, purpose: str, output_model: type) -> LLMResult:
    calls = [(kw, r) for kw, r in llm.results if kw["purpose"] == purpose]
    assert len(calls) == 1, f"expected one {purpose} call, got {len(calls)}"
    kwargs, result = calls[0]
    assert result.stop_reason != "max_tokens", (
        f"{purpose}: cut off at max_tokens={kwargs['max_tokens']} "
        f"({result.usage.output_tokens} output tokens, thinking included)"
    )
    assert result.stop_reason == "end_turn", f"{purpose}: stop_reason={result.stop_reason}"
    assert isinstance(result.parsed, output_model), f"{purpose}: answer did not parse: {result.text[:300]!r}"
    return result


def test_resume_facts_are_read_in_full(live_agent, llm):
    resume = RESUME.read_text(encoding="utf-8")
    assert 5000 < len(resume) < 12_000  # realistic, and within what extract_facts reads
    facts = extract_facts(live_agent, owner_name="Marisol Quentara-Vey", text=resume, source="resume.txt")
    assert_finished_and_parsed(llm, "intro_facts", FactList)
    assert facts is not None and len(facts) >= 5, facts
    assert any("Lanternfish" in f.content for f in facts)


def test_owner_chat_turn_parses(live_agent, llm, manager):
    result = live_agent.handle(tg(MANAGER_TG, "Hi Ava. In one sentence, what can you do for me while I'm in meetings?"))
    out = assert_finished_and_parsed(llm, "chat_turn", TurnOutput).parsed
    assert result.mode == "known" and "unparsed" not in result.notes and "budget" not in result.notes
    assert out.reply.strip() and result.reply.startswith(out.reply)


def test_intro_about_answer_becomes_facts(live_agent, llm):
    c = TestClient(create_app(live_agent), headers={"X-Requested-With": "superaide"})
    account = {"name": "Marisol Quentara-Vey", "email": "marisol@example.com", "password": "correct horse battery"}
    assert c.post("/auth/signup", json=account).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    assert (
        c.post("/app/onboarding", json={"step": "hello", "answer": {"choice": "Nice to meet you"}}).status_code == 200
    )
    about = (
        "I'm the CEO of Lanternfish Analytics, a 140-person logistics software company based in Lisbon with an "
        "engineering office in Rotterdam. Teodor Brannick is my co-founder and CTO, and Ruben Ferreira-Moss is my "
        "chief of staff. I prefer written updates to meetings, and I never take calls before 9am."
    )
    r = c.post("/app/onboarding", json={"step": "about", "answer": {"mode": "chat", "text": about}})
    assert r.status_code == 200 and r.json()["step"] == "people", r.text
    assert_finished_and_parsed(llm, "intro_facts", FactList)
    with session_scope() as s:
        learned = s.scalars(select(MemoryItem.content).where(MemoryItem.source == "intro_chat")).all()
    extracted = [m for m in learned if not m.startswith("Marisol Quentara-Vey says:")]
    assert len(extracted) >= 3, learned  # facts beyond the raw "X says: ..." note
