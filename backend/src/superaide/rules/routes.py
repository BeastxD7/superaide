"""Owner routes for time-travel rules (#52), under `/app/standing-orders`. 404 for the owner while off; others
get 401/403 first. Everything here is owner-only data."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.db.session import session_scope
from superaide.rules import timetravel as tt


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=3, max_length=1500)
    spec: tt.RuleSpec | None = None  # with it, no model is called


class JudgeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=60)
    agree: bool


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_agent().settings.time_travel_rules_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/standing-orders",
        tags=["Owner: standing orders"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def _settings() -> Any:
        return get_agent().settings

    @router.get("", summary="My standing orders and their track record")
    def listing(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {"rules": tt.listing(s)}

    @router.post("", summary="Write a standing order", description="Shows the rule back in plain words; it is a draft.")
    def create(body: CreateBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        spec = tt.clean(body.spec) if body.spec else tt.compile_text(get_agent(), body.text)
        if spec is None:
            raise HTTPException(422, "I couldn't turn that into a rule. Say who, which words, or which channel.")
        with session_scope() as s:
            rule = tt.create(s, body.text, spec)
            if rule is None:
                raise HTTPException(409, f"You can have at most {tt.MAX_RULES} standing orders.")
            return {**rule, "plain": tt.describe(spec)}

    @router.put("/{rule_id}", summary="Change a rule (this voids its replay)")
    def change(rule_id: str, spec: tt.RuleSpec, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not tt.edit(s, rule_id, spec):
                raise HTTPException(404, "No such rule")
            return {"ok": True, "plain": tt.describe(tt.clean(spec))}

    @router.delete("/{rule_id}", summary="Delete a rule")
    def remove(rule_id: str, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not tt.delete(s, rule_id):
                raise HTTPException(404, "No such rule")
            return {"deleted": True}

    @router.post("/{rule_id}/replay", summary="Replay this rule against my last 90 days")
    def replay(rule_id: str, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            report = tt.replay(s, rule_id, _settings().time_travel_replay_days)
            if report is None:
                raise HTTPException(404, "No such rule")
            return report

    @router.post("/{rule_id}/activate", summary="Switch on in shadow mode (needs a replay)")
    def activate(rule_id: str, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            problem = tt.activate(s, rule_id)
        if problem is not None:
            raise HTTPException(409, problem)
        return {"status": "shadow"}

    @router.post("/{rule_id}/judge", summary="Say whether a firing was right")
    def judge(rule_id: str, body: JudgeBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not tt.judge(s, rule_id, body.event_id, body.agree, min_agreement=_settings().time_travel_min_agreement):
                raise HTTPException(404, "No such firing")
            return {"ok": True}

    @router.post("/{rule_id}/promote", summary="Let a rule act on its own (the owner only, after enough agreement)")
    def promote(rule_id: str, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        cfg = _settings()
        with session_scope() as s:
            problem = tt.promote(
                s, rule_id, after=cfg.time_travel_promote_after, min_agreement=cfg.time_travel_min_agreement
            )
        if problem is not None:
            raise HTTPException(409, problem)
        return {"status": "live"}

    return router
