"""/app/skill-releases: the cooldown on new skill versions and the reserved names (issue #129).

Behind SKILL_COOLDOWN_ENABLED (404 while off, after strangers are refused). Owner only, CSRF-checked.
No model call. Community skills stay untrusted; this only decides when a version may be installed."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.session import session_scope
from superaide.skills import cooldown
from superaide.skills import registry as skill_registry


class ReleaseBody(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$", max_length=120)
    version: str = Field(min_length=1, max_length=40)
    # Needed for a skill that isn't in the built-in catalog; a catalog skill's real effects are used instead.
    effects: list[str] = Field(default_factory=list, max_length=6)
    earned_tier: str = "none"
    patch_signature: str | None = Field(default=None, max_length=64)


class RetireBody(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$", max_length=120)
    reason: str = Field(default="", max_length=200)


def cooldown_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    from superaide.config import get_settings

    if not get_settings().skill_cooldown_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def make_skill_releases_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/skill-releases",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Skill releases and reserved names", description="404 while SKILL_COOLDOWN_ENABLED is off.")
    def releases(person_id: uuid.UUID = Depends(cooldown_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return cooldown.view(s)

    @router.post(
        "", summary="Record a skill release", description="Starts the cooldown clock; repeating it keeps the first."
    )
    def record(body: ReleaseBody, person_id: uuid.UUID = Depends(cooldown_owner)) -> dict[str, Any]:
        known = skill_registry.catalog().get(body.skill_id)
        effects = list(known.manifest.permissions) if known else body.effects
        bad = [e for e in effects if e not in cooldown.EFFECT_ORDER]
        if bad:
            raise HTTPException(422, f"Unknown effect class {bad[0]!r}")
        with session_scope() as s:
            try:
                cooldown.record_release(
                    s, body.skill_id, body.version, effects,
                    earned_tier=body.earned_tier, patch_signature=body.patch_signature,
                )  # fmt: skip
            except cooldown.CooldownError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return cooldown.view(s)

    @router.post("/retire", summary="Retire a skill name", description="The name is reserved for good.")
    def retire(body: RetireBody, person_id: uuid.UUID = Depends(cooldown_owner)) -> dict[str, Any]:
        with session_scope() as s:
            cooldown.retire_name(s, body.skill_id, body.reason)
            return cooldown.view(s)

    return router
