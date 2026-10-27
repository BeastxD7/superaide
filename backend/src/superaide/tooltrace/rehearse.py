"""Rehearse the owner's recent tasks under two configurations and compare what Ava would do (issue #110).

This is the engine behind three features (replay before an update, audition of candidate skills, the model-swap
drill). It re-runs real tasks from the owner's own history through the same owner loop Ava uses, with the
executor, not the skill, deciding what runs:

- Only a tool that reads local data (`recall`, `list_reminders`) really runs. Every other tool, including web
  and mail reads (network), and every write, send or spend, gets a canned answer and does nothing, so a
  rehearsal makes no network call to a third party and changes nothing. Both runs of a comparison use the
  same rule, so the only thing that differs is the configuration under test.
- Each task runs inside a database savepoint that is rolled back, and the model's calls draw from the small
  `learning` budget bucket (purpose `replay_turn`), so a rehearsal can stop early and says so.
- Only the owner's own past messages are used, and only inside the owner's own instance. What comes back is
  tool names, effect classes and argument hashes: never message text, tool results or anyone else's content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from superaide.brain.spend.budget import AgentFrozen, BudgetExceeded
from superaide.identity.models import Person
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.memory.audience import Audience
from superaide.memory.knowledge.profile import owner_brief
from superaide.memory.models import Event
from superaide.skills import registry
from superaide.tooltrace.models import ToolTrace
from superaide.tooltrace.recorder import Call, calls_of, digest
from superaide.tooltrace.replay import CANNED, Diff, Replayer, diff

PURPOSE = "replay_turn"
# The only tools a rehearsal lets run for real: they read the owner's own data in this database. Anything that
# could touch the network (web, mail, calendar) is answered with a canned result instead.
LOCAL_READS = frozenset({"recall", "list_reminders", "list_approvals"})


class RehearsalError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class LocalOnlyReplayer(Replayer):
    """A Replayer that also refuses every read that is not local."""

    def handle(self, name: str, args: dict[str, Any], real: Any) -> tuple[Any, bool]:
        if name in LOCAL_READS:
            return super().handle(name, args, real)
        h = digest(args)
        from superaide.tooltrace.effects import effect_of

        self.calls.append(Call(name, effect_of(name), h))
        self.stubbed += 1
        return CANNED, False


@dataclass(frozen=True)
class Variant:
    """One configuration to rehearse: a model, extra instructions, and/or extra (instruction-only) skills."""

    label: str
    model: str | None = None  # None: the owner's chat model
    brief_extra: str = ""  # appended to the owner brief (a prompt change)
    add_skills: tuple[str, ...] = ()  # catalogue skill ids added to the installed ones


@dataclass(frozen=True)
class Task:
    event_id: uuid.UUID
    at: datetime
    text: str  # the owner's own message; used to rehearse, never returned
    recorded: list[Call]


@dataclass
class TaskResult:
    index: int
    at: datetime
    recorded: int  # how many calls Ava made the first time
    runs: dict[str, list[Call]] = field(default_factory=dict)  # variant label -> the calls it makes in rehearsal


@dataclass
class Rehearsal:
    results: list[TaskResult]
    asked: int
    stopped: str | None = None  # why it ended early (budget, frozen), else None


def recent_tasks(session: Session, owner: Person, n: int) -> list[Task]:
    """The owner's last `n` own messages that led to tool calls, newest first."""
    turns = session.execute(
        select(ToolTrace.turn_id, func.max(ToolTrace.created_at))
        .where(ToolTrace.replay.is_(False), ToolTrace.turn_id.is_not(None), ToolTrace.agent_id == owner.agent_id)
        .group_by(ToolTrace.turn_id)
        .order_by(desc(func.max(ToolTrace.created_at)))
        .limit(max(n, 1) * 4)
    ).all()
    ids = [t for t, _ in turns if t is not None]
    if not ids:
        return []
    events = {
        e.id: e
        for e in session.scalars(
            select(Event).where(
                Event.id.in_(ids),
                Event.direction == "in",
                Event.agent_id == owner.agent_id,
                Event.resolved_person_id == owner.id,  # the owner's own message, never someone else's
            )
        )
    }
    out: list[Task] = []
    for turn_id, _ in turns:
        ev = events.get(turn_id) if turn_id is not None else None
        if ev is not None and ev.content.strip():
            out.append(Task(ev.id, ev.created_at, ev.content, calls_of(session, ev.id)))
        if len(out) >= n:
            break
    return out


def _sender(owner: Person) -> Sender:
    return Sender(identity=cast(Any, None), person=owner, trust=int(Trust.LINKED))


def run_task(agent: Any, session: Session, owner: Person, task: Task, variant: Variant) -> list[Call]:
    """Rehearse one task under one variant and return the calls it makes. Nothing is kept."""
    from superaide.brain.loop import OwnerTurn

    sender = _sender(owner)
    aud = Audience(person_ids={owner.id}, has_unknown=False, min_trust=int(Trust.LINKED), all_internal=True)
    event = session.get(Event, task.event_id)
    if event is None:
        return []
    brief = owner_brief(session, sender, aud)
    if variant.brief_extra:
        brief = (brief + "\n\n" if brief else "") + variant.brief_extra
    replayer = LocalOnlyReplayer()
    savepoint = session.begin_nested()
    try:
        turn = OwnerTurn(
            llm=agent.llm, model=variant.model or agent.settings.model_chat, agent_name=agent.name, session=session,
            sender=sender, audience=aud, brief=brief, inbound=event, web=False, emit=None, learns=False,
        )  # fmt: skip
        turn.replay, turn.purpose = replayer, PURPOSE
        if variant.add_skills:
            have = registry.active(session)
            known = registry.catalog()
            missing = [i for i in variant.add_skills if i not in known]
            if missing:
                raise RehearsalError(404, "No such skill: " + ", ".join(missing))
            turn._skills = [*have, *[known[i] for i in variant.add_skills if known[i] not in have]]
        turn.run([{"role": "user", "content": task.text}])
    finally:
        savepoint.rollback()  # whatever the loop wrote (it stubs writes, but notes and traces too) is undone
    return replayer.calls


def rehearse(agent: Any, session: Session, owner: Person, tasks: list[Task], variants: list[Variant]) -> Rehearsal:
    """Every variant on every task. A refused or failed model call ends the run with what was done so far."""
    results = [TaskResult(i, t.at, len(t.recorded)) for i, t in enumerate(tasks)]
    stopped = None
    try:
        for res, task in zip(results, tasks, strict=True):
            for v in variants:
                res.runs[v.label] = run_task(agent, session, owner, task, v)
    except BudgetExceeded as exc:
        stopped = f"spend limit: {exc}"
    except AgentFrozen:
        stopped = "Ava is paused"
    results = [r for r in results if len(r.runs) == len(variants)]  # only tasks every variant finished
    return Rehearsal(results, asked=len(tasks), stopped=stopped)


def call_view(c: Call) -> dict[str, str]:
    """A call as a person may see it: names and a short hash, nothing else."""
    return {"tool": c.tool, "effect": c.effect, "args": c.args_hash[:12]}


def diff_view(d: Diff) -> dict[str, Any]:
    return {
        "same": d.same,
        "summary": d.summary(),
        "only_before": [call_view(c) for c in d.only_before],
        "only_after": [call_view(c) for c in d.only_after],
    }


def pair(r: TaskResult, a: str, b: str) -> Diff:
    return diff(r.runs[a], r.runs[b])
