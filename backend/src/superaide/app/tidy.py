"""/app/tidy: what the nightly sleep job changed in the owner's notebook, and undo (#415).

Owner only, and only the owner's own changes: rows are filtered to the signed-in owner and to notes the owner is
the subject of and alone may see (the job only ever touches those), and Row-Level Security keeps other instances
out. 404 while SLEEP_JOB_ENABLED is off (there is nothing to show). Newest first, paged with a cursor (ADR 0014).
Undo is a compensating write in code (memory/consolidation/changes.py); no model is involved.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased

from superaide.app.episodes import episodes_owner
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.memory.consolidation import changes
from superaide.memory.models import ConsolidationChange, MemoryItem
from superaide.platform.db.session import session_scope
from superaide.platform.pagination import SortKey, bind_for, cursor_param, limit_param, paginate


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
                    ConsolidationChange.kind.in_(changes.KINDS),
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
                        "can_undo": gone is None and changes.retired_state(retired) == c.after["retired"],
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
                row = changes.undo(s, person_id, change_id)
            except changes.ChangeError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            return {"status": "undone", "change_id": str(change_id), "undo_id": str(row.id)}

    return router
