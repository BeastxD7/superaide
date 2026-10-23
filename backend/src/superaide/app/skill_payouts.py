"""/app/skill-payouts: the creator payout ledger, a dry-run simulation, bonds and the null settlement (issue #132).

Behind PAYOUTS_ENABLED (404 while off, after strangers are refused). Owner only, CSRF-checked. Nothing here moves
money: the only settlement provider is the null one. No model call."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.session import session_scope
from superaide.skills import payouts


class BondBody(BaseModel):
    creator: str = Field(min_length=1, max_length=80)
    amount_minor: int = Field(ge=1, le=100_000_000)


class RefundBody(BondBody):
    skill_id: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$", max_length=120)
    reason: str = Field(default="", max_length=200)


def payouts_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    if not payouts.enabled():
        raise HTTPException(404, "Not Found")
    return person_id


def make_skill_payouts_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/skill-payouts",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Ledger, balances, bonds and the dry-run simulation", description="404 while off.")
    def overview(person_id: uuid.UUID = Depends(payouts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return payouts.view(s)

    @router.post("/accrue", summary="Write ledger lines for accepted outcomes and clawbacks")
    def accrue(person_id: uuid.UUID = Depends(payouts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return {"made": payouts.accrue(s), **payouts.view(s)}

    @router.post("/bond", summary="Record a refund bond a creator posted (no money moves)")
    def bond(body: BondBody, person_id: uuid.UUID = Depends(payouts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                payouts.post_bond(s, body.creator, body.amount_minor)
            except payouts.PayoutError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return payouts.view(s)

    @router.post("/refund", summary="Owe a harmed owner a refund, drawn from the creator's bond")
    def refund(body: RefundBody, person_id: uuid.UUID = Depends(payouts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                payouts.refund_from_bond(s, body.creator, body.skill_id, body.amount_minor, body.reason)
            except payouts.PayoutError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return payouts.view(s)

    @router.post("/settle", summary="Ask the settlement provider to pay what is due (the null one pays nothing)")
    def settle(person_id: uuid.UUID = Depends(payouts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                report = payouts.settle_due(s)
            except payouts.PayoutError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return {"provider": report.provider, "due": report.due, "settled_minor": report.settled_minor,
                    "notes": report.notes}  # fmt: skip

    return router
