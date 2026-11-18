"""/app/episodes and /app/tidy: the owner's list of conversation summaries from the nightly sleep job (#50).

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
from sqlalchemy.orm import aliased

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.memory.api import KINDS, ChangeError, retired_state
from superaide.memory.api import undo as undo_change
from superaide.memory.models import ConsolidationChange, Episode, MemoryItem
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.pagination import SortKey, bind_for, cursor_param, limit_param, paginate

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


def make_tidy_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/tidy",
        tags=["Owner: episodes"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="What Ava tidied overnight",
        description="Notes the nightly job retired or merged, newest first, with why and where each came from. "
        "Paged: `limit` (1 to 100, default 25) and `cursor`. 404 while SLEEP_JOB_ENABLED is off.",
    )
    def tidy_log(
        person_id: uuid.UUID = Depends(episodes_owner),
        limit: int = limit_param(25, 100),
        cursor: str | None = cursor_param(),
    ) -> dict[str, Any]:
        with session_scope() as s:
            kept = aliased(MemoryItem)
            stmt: Any = (
                select(ConsolidationChange, MemoryItem, kept)
                .join(MemoryItem, MemoryItem.id == ConsolidationChange.memory_id)
                .outerjoin(kept, kept.id == ConsolidationChange.kept_id)
                .where(
                    ConsolidationChange.owner_person_id == person_id,
                    ConsolidationChange.kind.in_(KINDS),
                    MemoryItem.subject_person_ids.contains([person_id]),  # only notes the owner may see
                    MemoryItem.allowed_kind == "persons",
                    MemoryItem.allowed_person_ids == [person_id],
                )
            )
            page = paginate(
                s,
                stmt,
                [SortKey(ConsolidationChange.created_at), SortKey(ConsolidationChange.id)],
                endpoint="tidy",
                bind=bind_for(person_id),
                limit=limit,
                cursor=cursor,
                maximum=100,
                entity_of=lambda row: row[0],
                build=lambda row: row,
            )
            undone = {
                u.undoes_id: u.created_at
                for u in s.scalars(
                    select(ConsolidationChange).where(
                        ConsolidationChange.undoes_id.in_([c.id for c, _, _ in page.items])
                    )
                )
            }
            items = []
            for c, retired, keeper in page.items:
                gone = undone.get(c.id)
                items.append(
                    {
                        "id": str(c.id),
                        "run_id": str(c.run_id),
                        "at": c.created_at.isoformat(),
                        "kind": c.kind,
                        "reason": c.reason,
                        "decided_by": c.decided_by,
                        "source": c.source,
                        "retired": {"id": str(retired.id), "content": retired.content},
                        "kept": {"id": str(keeper.id), "content": keeper.content} if keeper is not None else None,
                        "before": c.before,
                        "after": c.after,
                        "undone_at": gone.isoformat() if gone else None,
                        "can_undo": gone is None and retired_state(retired) == c.after["retired"],
                    }
                )
            return {"items": items, "next_cursor": page.next_cursor, "has_more": page.has_more}

    @router.post(
        "/{change_id}/undo",
        summary="Undo one tidy",
        description="Puts the note back as it was. Appends an undo record; nothing is deleted. 409 if it was "
        "already undone or the note has changed since.",
    )
    def undo(change_id: uuid.UUID, person_id: uuid.UUID = Depends(episodes_owner)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                row = undo_change(s, person_id, change_id)
            except ChangeError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            return {"status": "undone", "change_id": str(change_id), "undo_id": str(row.id)}

    return router
