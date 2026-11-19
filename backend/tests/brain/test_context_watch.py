# ci-module: brain
"""A server that reads fewer prompt tokens than were sent has cut the prompt: audited once, shown, owner told once."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from superaide import doctor
from superaide.brain import context_watch, keyhealth
from superaide.brain.llm import FakeLLM, StepResult
from superaide.brain.loop import OwnerTurn
from superaide.brain.models import LlmProvider
from superaide.brain.spend.pricing import UsageCounts
from superaide.memory.models import Event
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, turn_for  # noqa: F401  (fixture)


class Cutting(FakeLLM):
    """A fake OpenAI-compatible server that reports `reported` prompt tokens whatever it was sent."""

    reported = 2_050

    def step(self, **kw: Any) -> StepResult:
        out = super().step(**kw)
        out.usage = UsageCounts(input_tokens=self.reported)
        return out


def local_model(s, window: int | None = 4_096) -> str:
    """An Ollama provider whose window the owner set (the override lives in the provider's meta)."""
    s.add(
        LlmProvider(
            key="ollama", preset="ollama", base_url="http://localhost:11434/v1", meta={"context_window": window}
        )
    )
    s.flush()
    return "ollama::qwen3:8b"


def big_turn(s, reported: int) -> OwnerTurn:
    turn = turn_for(s, Script("ok"))
    turn.model = local_model(s) if not s.scalar(select(LlmProvider)) else "ollama::qwen3:8b"
    turn.llm = Cutting(Script("ok"))
    turn.llm.reported = reported
    turn.brief = "Rule: be kind. " * 2_000  # about 7,000 tokens once estimated
    return turn


def rows(s, action: str) -> list[AuditLog]:
    return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_a_cut_prompt_is_audited_once_shown_and_told_to_the_owner_once(owner_client):  # noqa: F811
    with session_scope() as s:
        for _ in range(3):
            big_turn(s, 2_050).run([{"role": "user", "content": "hi"}])
        cut = rows(s, "prompt_truncated")
        assert len(cut) == 1, "one audit row, not one per turn"
        d = cut[0].detail
        assert (
            d["reported"] == 2_050
            and d["sent_estimate"] > 5_000
            and d["window"] == 4_096
            and d["window_source"] == "override"
        )
        found = keyhealth.notice(s, get_settings())
        assert found and found["reason"] == keyhealth.CONTEXT_CUT
        told = [e for e in s.scalars(select(Event)) if (e.meta or {}).get("kind") == "prompt_truncated"]
        assert len(told) == 1, "the owner is told once, not on every turn"
    check = doctor.ai_key(doctor.Context(settings=get_settings()))  # its own session: the turns are committed
    assert check[0].status == doctor.WARN and "tokens" in check[0].cause


def test_a_prompt_the_server_read_whole_is_not_flagged(owner_client):  # noqa: F811
    with session_scope() as s:
        turn = big_turn(s, 6_500)
        turn.run([{"role": "user", "content": "hi"}])
        assert rows(s, "prompt_truncated") == []
        assert context_watch.current(s) is None


def test_fake_models_that_report_nothing_never_trip_it(owner_client):  # noqa: F811
    with session_scope() as s:
        turn_for(s, Script("ok")).run([{"role": "user", "content": "hi"}])
        assert rows(s, "prompt_truncated") == []
