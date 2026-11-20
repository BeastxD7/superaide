"""Owner routes for standing rules (#486), under `/app/standing-rules`. Owner-only (401/403 for everyone else); the
model never calls these."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.db.session import session_scope
from superaide.policy import standing
from superaide.policy.models import StandingRule


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["allow_statement", "forbid"]
    scope: Literal["everyone", "contact"] = "everyone"
    text: str = Field(default="", max_length=400)
    topic: str = Field(default="", max_length=24)
    contact_person_id: uuid.UUID | None = None
    expires_at: datetime | None = None


def _view(r: StandingRule) -> dict[str, Any]:
    return {
        "id": str(r.id), "kind": r.kind, "scope": r.scope,
        "contact_person_id": str(r.contact_person_id) if r.contact_person_id else None,
        "text": r.text, "topic": r.topic, "topic_label": standing.FORBID_TOPICS.get(r.topic, ""),
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
    }  # fmt: skip


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        return person_id

    router = APIRouter(
        prefix="/app/standing-rules",
        tags=["Owner: standing rules"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="My standing rules, and the topics Ava can be told never to discuss")
    def listing(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {
                "rules": [_view(r) for r in standing.listing(s, person_id)],
                "topics": standing.FORBID_TOPICS,
                "max_rules": standing.MAX_RULES,
            }

    @router.post("", summary="Save a rule", description="The owner's explicit save; Ava only ever proposes one.")
    def create(body: RuleBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                rule = standing.create(
                    s, person_id, kind=body.kind, scope=body.scope, text=body.text, topic=body.topic,
                    contact_person_id=body.contact_person_id, expires_at=body.expires_at,
                )  # fmt: skip
            except standing.RuleError as e:
                raise HTTPException(422, str(e)) from e
            return _view(rule)

    @router.put("/{rule_id}", summary="Change a rule's words, topic or end date")
    def change(rule_id: uuid.UUID, body: RuleBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                rule = standing.update(
                    s, person_id, rule_id, text=body.text, topic=body.topic, expires_at=body.expires_at
                )
            except standing.RuleError as e:
                raise HTTPException(422, str(e)) from e
            if rule is None:
                raise HTTPException(404, "No such rule")
            return _view(rule)

    @router.delete("/{rule_id}", summary="Stop a rule")
    def remove(rule_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not standing.revoke(s, person_id, rule_id):
                raise HTTPException(404, "No such rule")
            return {"deleted": True}

    return router
