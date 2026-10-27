"""/app/episodes: the owner's list of conversation summaries from the nightly sleep job (#50).

Owner only. An episode summary is a digest of conversations, so nobody but the owner is ever shown one: the
list is filtered to the signed-in owner's own episodes whose audience includes the owner, and Row-Level
Security keeps other instances out. 404 while SLEEP_JOB_ENABLED is off (there are none to show). The owner can
erase an episode; that deletes the summary only, the event log stays the source of truth. No model call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.memory.models import Episode
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope

LIMIT = 100


def episodes_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    """The owner, once the sleep job is on for this server (strangers are refused first, by owner_person_id)."""
    if not get_settings().sleep_job_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def view(e: Episode, owner_id: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "channel": e.channel,
        "started_at": e.started_at.isoformat(),
        "ended_at": e.ended_at.isoformat(),
        "messages": len(e.event_ids),
        "summary": e.summary,
        "summary_by": e.summary_by,
        "hints": list(e.hints),
        "others_present": e.audience_has_unknown or set(e.audience_person_ids) != {owner_id},
    }


def make_episodes_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/episodes",
        tags=["Owner: episodes"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="Conversation summaries from the nightly job",
        description="The owner's own episodes, newest first. 404 while SLEEP_JOB_ENABLED is off.",
    )
    def episodes(person_id: uuid.UUID = Depends(episodes_owner)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(
                select(Episode)
                .where(Episode.owner_person_id == person_id, Episode.audience_person_ids.contains([person_id]))
                .order_by(Episode.started_at.desc())
                .limit(LIMIT)
            ).all()
            return {"episodes": [view(e, person_id) for e in rows]}

    @router.post(
        "/{episode_id}/erase",
        summary="Erase one summary",
        description="Deletes the summary only. The messages themselves are not touched.",
    )
    def erase(episode_id: uuid.UUID, person_id: uuid.UUID = Depends(episodes_owner)) -> dict[str, bool]:
        with session_scope() as s:
            e = s.get(Episode, episode_id)
            if e is None or e.owner_person_id != person_id:
                raise HTTPException(404, "Not found")
            s.delete(e)
            audit(s, "app", "episode_erased", episode_id=episode_id, person_id=person_id)
            return {"erased": True}

    return router
