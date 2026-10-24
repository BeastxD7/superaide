"""ADR 0008 WP6: one owner across the app, Telegram and Slack, scripted end to end (offline).

The model is fake, so this measures what Ava is *given*: whether the fact or the earlier
message is in her prompt (a hit), labelled with its channel (attribution), never in anyone
else's prompt (a leak), and whether she's told she learns (so she can't say she forgets).
The same script runs against real Haiku in tests/live when asked for.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.llm import FakeLLM
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.context.audience import Audience
from superaide.context.recall import recall
from superaide.db.models import ChannelIdentity, MemoryItem
from superaide.db.session import session_scope
from superaide.identity.trust import Trust
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.facts import Fact, FactList
from superaide.main import create_app
from tests.conftest import COLLEAGUE_SLACK, OUR_TEAM, sl, tg

load()
PHONE, SLACK_ME = "777001", "U_JORDAN"
LEARNED = "Jordan does not like the colour black."
CHANGED = "Jordan likes the colour black now."
LEARNS = "learn from what your owner tells you"


class Ava:
    """Fake Claude: scripted extraction, plain replies; keeps every chat prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.next_facts: list[Fact] = []

    def __call__(self, purpose: str, system: Any, messages: Any, output_model: Any) -> Any:
        if purpose == "intro_facts":
            known = messages[0]["content"]
            facts = []
            for f in self.next_facts:
                ref = next(
                    (int(ln.split(":")[0]) for ln in known.splitlines() if "colour black" in ln and ln[:1].isdigit()),
                    None,
                )
                facts.append(f.model_copy(update={k: ref for k in ("same_as", "replaces") if getattr(f, k) == -1}))
            self.next_facts = []
            return FactList(facts=facts)
        self.prompts.append("\n".join(b.text for b in system) + "\n" + str(messages))
        if output_model is not None:
            return TurnOutput(
                reply="Noted.",
                requested_action=RequestedAction(kind="none", summary=""),
                prediction=Prediction(expectation="they reply", confidence=0.5),
            )
        return "Noted."


@pytest.fixture
def world(agent, colleague, monkeypatch):
    ava = Ava()
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: FakeLLM(ava))
    agent.llm = FakeLLM(ava)
    agent.settings.web_tools_enabled = False
    app = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    app.post(
        "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": "correct horse battery"}
    )
    app.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        me = hired_owner(s)
        for provider, scope, key in (("telegram", "", PHONE), ("slack", OUR_TEAM, SLACK_ME)):
            s.add(ChannelIdentity(person_id=me.id, provider=provider, tenant_scope=scope, stable_key=key,
                                  trust_level=int(Trust.LINKED), verification={"method": "test"}))  # fmt: skip
    return app, ava


def _last(ava: Ava) -> str:
    return ava.prompts[-1]


def test_one_owner_three_channels(world, agent):
    app, ava = world
    m = {"hits": 0, "hit_targets": 3, "leaks": 0, "attributed": 0, "told_she_learns": 0}

    # 1. Telegram: a preference, typo and all. Ava is told she learns from it.
    ava.next_facts = [Fact(content=LEARNED, category="preference", confidence=1)]
    agent.handle(tg(PHONE, "btw i dont like black clour at all!"))
    m["told_she_learns"] += LEARNS in _last(ava)

    # 2. The app, seconds later, before learning has run: the Telegram turn is in the prompt.
    app.post("/app/chat", json={"text": "what colours do I dislike?"})
    m["hits"] += "black clour" in _last(ava)
    m["attributed"] += "[On Telegram · " in _last(ava)
    run_due()  # learning catches up

    # 3. The app, in other words: pinned in the brief, attached before the model runs, and recallable.
    app.post("/app/chat", json={"text": "color I hate?"})
    with session_scope() as s:
        me = hired_owner(s)
        found = [x.text for x in recall(s, Audience(person_ids={me.id}, min_trust=2), "color I hate")]
    m["hits"] += LEARNED in _last(ava) and LEARNED in found

    # 4. Slack DM, said again differently: still one fact, now with two sources.
    ava.next_facts = [Fact(content="Jordan dislikes the color black.", category="preference", confidence=1, same_as=-1)]
    agent.handle(sl(SLACK_ME, "I really can't stand black as a colour"))
    run_due()
    with session_scope() as s:
        live = s.scalars(
            select(MemoryItem).where(MemoryItem.category == "preference", MemoryItem.valid_to.is_(None))
        ).all()
        assert [(x.content, len(x.source_event_ids)) for x in live] == [(LEARNED, 2)]

    # 5. Groups: a colleague asks in a Slack channel, the owner asks in a Telegram group.
    agent.handle(sl(COLLEAGUE_SLACK, "what colour does Jordan dislike?", chat_kind="group", channel="C_TEAM"))
    m["leaks"] += "colour black" in _last(ava) or "black clour" in _last(ava)
    agent.handle(tg(PHONE, "what colour do I dislike?", chat_kind="group", chat_id="-100500"))
    m["leaks"] += "colour black" in _last(ava) or "black clour" in _last(ava) or "On Telegram" in _last(ava)

    # 6. The colleague's own DM.
    agent.handle(sl(COLLEAGUE_SLACK, "does Jordan like black?"))
    m["leaks"] += "colour black" in _last(ava) or "black clour" in _last(ava)

    # 7. The owner changes their mind; the next answer is built on the new fact only.
    ava.next_facts = [Fact(content=CHANGED, category="preference", confidence=1, replaces=-1)]
    app.post("/app/chat", json={"text": "Actually I've come round to black, I like it now"})
    run_due()
    app.post("/app/chat", json={"text": "what colour do I like?"})
    brief = _last(ava).split("What you know about them", 1)[-1].split("Skills you have", 1)[0]
    m["hits"] += CHANGED in brief and LEARNED not in brief

    print("\nADR 0008 scenario metrics:", m)
    assert m == {"hits": 3, "hit_targets": 3, "leaks": 0, "attributed": 1, "told_she_learns": 1}
