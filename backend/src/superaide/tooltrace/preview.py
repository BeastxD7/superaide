"""Replay before update (issue #110, use 1): "this would have done 3 things differently".

Before a skill, a prompt or a model change goes live, replay the owner's last N tasks with the current setup and
with the change, effects stubbed (see rehearse.py), and show the owner how the calls differ by name and argument
hash. The owner approves or rejects. Approving a *skill* installs it (with the permissions the owner confirms,
exactly as the install route does); approving a prompt or model change records the decision and says it was not
applied, because those live in settings the owner changes there. Off unless REPLAY_BEFORE_UPDATE_ENABLED.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from superaide.platform.audit import audit
from superaide.platform.db.models import Person
from superaide.skills import registry
from superaide.tooltrace import rehearse
from superaide.tooltrace.models import UpdatePreview

CURRENT = rehearse.Variant("current")
KINDS = ("skill", "prompt", "model")


def _variant(kind: str, skill_id: str, model: str, prompt: str) -> tuple[rehearse.Variant, str, dict[str, Any]]:
    if kind == "skill":
        if skill_id not in registry.catalog():
            raise rehearse.RehearsalError(404, "No such capability")
        return rehearse.Variant("change", add_skills=(skill_id,)), skill_id, {"skill_id": skill_id}
    if kind == "model":
        if not model.strip():
            raise rehearse.RehearsalError(422, "Name the model to try.")
        return rehearse.Variant("change", model=model.strip()), model.strip()[:120], {"model": model.strip()}
    if not prompt.strip():
        raise rehearse.RehearsalError(422, "Give the instructions to try.")
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    return rehearse.Variant("change", brief_extra=prompt), "prompt change", {"sha256": digest, "chars": len(prompt)}


def create(
    agent: Any, session: Session, owner: Person, *, kind: str, skill_id: str = "", model: str = "",
    prompt: str = "", tasks: int | None = None,
) -> UpdatePreview:  # fmt: skip
    if kind not in KINDS:
        raise rehearse.RehearsalError(422, "Choose skill, prompt or model.")
    variant, label, detail = _variant(kind, skill_id, model, prompt)
    cap = int(agent.settings.replay_max_tasks)
    todo = rehearse.recent_tasks(session, owner, max(1, min(tasks or cap, cap)))
    if not todo:
        raise rehearse.RehearsalError(409, "There is nothing to replay yet: Ava has not done any tasks for you.")
    run = rehearse.rehearse(agent, session, owner, todo, [CURRENT, variant])
    summary = []
    for r in run.results:
        d = rehearse.diff_view(rehearse.pair(r, CURRENT.label, variant.label))
        summary.append({"task": r.index + 1, "calls_now": len(r.runs[CURRENT.label]), **d})
    row = UpdatePreview(
        owner_person_id=owner.id, kind=kind, label=label, detail=detail, tasks_asked=run.asked,
        tasks_done=len(run.results), changed=sum(1 for x in summary if not x["same"]), summary=summary,
        stopped=(run.stopped or "")[:120],
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(
        session, "replay", "update_previewed", preview_id=row.id, kind=kind, tasks=row.tasks_done, changed=row.changed
    )
    return row


def headline(row: UpdatePreview) -> str:
    if not row.tasks_done:
        return "I could not replay any task."
    if row.changed == 0:
        return f"No difference on your last {row.tasks_done} tasks."
    return f"{row.changed} of your last {row.tasks_done} tasks would go differently."


def _mine(session: Session, owner: Person, preview_id: uuid.UUID) -> UpdatePreview:
    row = session.get(UpdatePreview, preview_id)
    if row is None or row.owner_person_id != owner.id:
        raise rehearse.RehearsalError(404, "Not found")
    if row.status != "pending":
        raise rehearse.RehearsalError(409, f"This was already {row.status}.")
    return row


def approve(session: Session, owner: Person, preview_id: uuid.UUID, accepted: list[str]) -> tuple[UpdatePreview, bool]:
    """(the preview, whether the change was applied). Only a skill is applied here."""
    row = _mine(session, owner, preview_id)
    applied = False
    if row.kind == "skill":
        try:
            registry.install(session, str(row.detail.get("skill_id")), accepted)
        except registry.SkillUnavailable as exc:
            raise rehearse.RehearsalError(exc.status, str(exc)) from exc
        applied = True
    row.status, row.decided_by_person_id, row.decided_at = "approved", owner.id, datetime.now(UTC)
    audit(session, "replay", "update_approved", preview_id=row.id, kind=row.kind, applied=applied)
    return row, applied


def reject(session: Session, owner: Person, preview_id: uuid.UUID) -> UpdatePreview:
    row = _mine(session, owner, preview_id)
    row.status, row.decided_by_person_id, row.decided_at = "rejected", owner.id, datetime.now(UTC)
    audit(session, "replay", "update_rejected", preview_id=row.id, kind=row.kind)
    return row
