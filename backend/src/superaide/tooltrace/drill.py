"""Model-swap drill (issue #110, use 3): replay the owner's last tasks on two models and report where the call
sequences differ, before the owner changes the model Ava runs on.

Both models run the same tasks with the same stubs (see rehearse.py): only local reads run, every other effect
is answered with a canned result, nothing is changed or sent and there is no network call. The report says how
many tasks would change, where in each sequence the first difference is, and which tools only one model reached
for, as tool names and short argument hashes. Off unless MODEL_DRILL_ENABLED.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from superaide.brain.spend.pricing import UnknownModelPrice, price_for
from superaide.platform.audit import audit
from superaide.platform.db.models import Person
from superaide.tooltrace import rehearse
from superaide.tooltrace.recorder import Call
from superaide.tooltrace.replay import drill as headline


def _first_difference(a: list[Call], b: list[Call]) -> int | None:
    """1-based position of the first call where the two runs part ways; None when identical."""
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return i + 1
    return None if len(a) == len(b) else min(len(a), len(b)) + 1


def _check(model: str) -> str:
    model = model.strip()
    if not model:
        raise rehearse.RehearsalError(422, "Name both models.")
    if ":" not in model:  # a bare id runs on Claude and must have a price, or the spend guard refuses it
        try:
            price_for(model)
        except UnknownModelPrice as exc:
            raise rehearse.RehearsalError(422, f"There is no price listed for {model}, so it can't be run.") from exc
    return model


def run(
    agent: Any, session: Session, owner: Person, model_a: str, model_b: str, tasks: int | None = None
) -> dict[str, Any]:
    a = _check(model_a or agent.settings.model_chat)
    b = _check(model_b)
    if a == b:
        raise rehearse.RehearsalError(422, "Pick two different models.")
    cap = int(agent.settings.drill_max_tasks)
    todo = rehearse.recent_tasks(session, owner, max(1, min(tasks or cap, cap)))
    if not todo:
        raise rehearse.RehearsalError(409, "There is nothing to replay yet: Ava has not done any tasks for you.")
    try:
        done = rehearse.rehearse(
            agent, session, owner, todo, [rehearse.Variant(a, model=a), rehearse.Variant(b, model=b)]
        )
    except rehearse.RehearsalError:
        raise
    except Exception as exc:  # a model that can't be reached or isn't configured
        raise rehearse.RehearsalError(502, f"One of the models could not be run ({type(exc).__name__}).") from exc
    only_a: Counter[str] = Counter()
    only_b: Counter[str] = Counter()
    per_task = []
    for r in done.results:
        ca, cb = r.runs[a], r.runs[b]
        d = rehearse.diff_view(rehearse.pair(r, a, b))
        for c in rehearse.pair(r, a, b).only_before:
            only_a[c.tool] += 1
        for c in rehearse.pair(r, a, b).only_after:
            only_b[c.tool] += 1
        per_task.append(
            {
                "task": r.index + 1,
                "calls_a": len(ca),
                "calls_b": len(cb),
                "first_difference_at": _first_difference(ca, cb),
                **d,
            }  # fmt: skip
        )
    audit(session, "replay", "model_drill_run", tasks=len(done.results), stopped=done.stopped or "")
    return {
        "model_a": a,
        "model_b": b,
        "headline": headline([(r.runs[a], r.runs[b]) for r in done.results]),
        "tasks_asked": done.asked,
        "tasks_done": len(done.results),
        "changed": sum(1 for t in per_task if not t["same"]),
        "stopped": done.stopped,
        "only_model_a": dict(only_a),
        "only_model_b": dict(only_b),
        "tasks": per_task,
    }
