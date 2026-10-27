"""Audition (issue #110, use 2): put two or three candidate skills through the owner's last tasks, side by side.

Each candidate skill is added to what Ava already has and every task is rehearsed with it (see rehearse.py), so
the owner sees what each would have done on work they really asked for before installing any of them. It judges
the skill's instructions and tool choices only: the executor stubs every effect and answers every non-local
read with a canned result, so there is no network call and nothing is changed or sent, whatever the skill
declares. What comes back is tool names and short argument hashes. Off unless AUDITION_ENABLED.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.platform.audit import audit
from superaide.skills import registry
from superaide.tooltrace import rehearse
from superaide.tooltrace.replay import diff

MIN_CANDIDATES, MAX_CANDIDATES = 2, 3
WRITES = frozenset({"write_own", "write_external", "send", "spend"})


def candidates() -> list[dict[str, Any]]:
    """The skills that can be auditioned: the built-in catalogue, with what each declares."""
    out = []
    for skill_id, skill in registry.catalog().items():
        m = skill.manifest
        out.append({"id": skill_id, "title": m.title, "summary": m.summary, "tools": [t.name for t in m.tools]})
    return out


def run(agent: Any, session: Session, owner: Person, skill_ids: list[str], tasks: int | None = None) -> dict[str, Any]:
    ids = list(dict.fromkeys(skill_ids))  # in order, without repeats
    if not MIN_CANDIDATES <= len(ids) <= MAX_CANDIDATES:
        raise rehearse.RehearsalError(422, f"Choose {MIN_CANDIDATES} or {MAX_CANDIDATES} skills to compare.")
    known = registry.catalog()
    if missing := [i for i in ids if i not in known]:
        raise rehearse.RehearsalError(404, "No such capability: " + ", ".join(missing))
    cap = int(agent.settings.audition_max_tasks)
    todo = rehearse.recent_tasks(session, owner, max(1, min(tasks or cap, cap)))
    if not todo:
        raise rehearse.RehearsalError(409, "There is nothing to audition on yet: Ava has not done any tasks for you.")
    variants = [rehearse.Variant(i, add_skills=(i,)) for i in ids]
    done = rehearse.rehearse(agent, session, owner, todo, variants)
    per_task, differ = [], {pair: 0 for pair in combinations(ids, 2)}
    totals = {i: {"calls": 0, "would_change_things": 0} for i in ids}
    for r in done.results:
        row: dict[str, Any] = {"task": r.index + 1, "calls_before": r.recorded, "candidates": {}}
        for i in ids:
            calls = r.runs[i]
            totals[i]["calls"] += len(calls)
            totals[i]["would_change_things"] += sum(1 for c in calls if c.effect in WRITES)
            row["candidates"][i] = [rehearse.call_view(c) for c in calls]
        for a, b in differ:
            if not diff(r.runs[a], r.runs[b]).same:
                differ[(a, b)] += 1
        row["all_agree"] = all(diff(r.runs[ids[0]], r.runs[i]).same for i in ids[1:])
        per_task.append(row)
    audit(session, "replay", "audition_run", skills=len(ids), tasks=len(done.results), stopped=done.stopped or "")
    return {
        "skills": ids,
        "tasks_asked": done.asked,
        "tasks_done": len(done.results),
        "stopped": done.stopped,
        "agree_on": sum(1 for t in per_task if t["all_agree"]),
        "totals": totals,
        "differ": [{"a": a, "b": b, "tasks": n} for (a, b), n in differ.items()],
        "tasks": per_task,
    }
