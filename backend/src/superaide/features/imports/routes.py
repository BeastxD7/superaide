"""/app/import-leases: bring in cards converted from a third-party export, under a lease (#133).

Owner only like the rest of /app (session cookie, X-Requested-With on writes, the owner's instance
in scope). 404 while IMPORT_LEASE_ENABLED is off, after the 401/403 checks. The server takes only
the card schema, never a raw file.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from superaide.app.deps import owner_person_id
from superaide.features.imports import lease
from superaide.features.imports.cards import CardBatch, LeaseRequest
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.config import get_settings
from superaide.platform.db.models import Person
from superaide.platform.db.session import require, session_scope


def owner_with_leases(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    if not get_settings().import_lease_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/import-leases",
        tags=["Imports"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="Your imports",
        description="Each import lease: what it may hold, when it ends, how many cards it holds now.",
    )
    def list_leases(person_id: uuid.UUID = Depends(owner_with_leases)) -> list[dict[str, Any]]:
        with session_scope() as s:
            return lease.view(s, require(s, Person, person_id))

    @router.post(
        "",
        summary="Open an import lease",
        description=(
            "Names the source, the scopes the owner allows and how many days (1 to 30, default 7). Cards are "
            "added next; when the lease ends every card goes."
        ),
    )
    def open_lease(body: LeaseRequest, person_id: uuid.UUID = Depends(owner_with_leases)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            lease.sweep_expired(s, owner)
            made = lease.open_lease(s, owner, body)
            return {"id": str(made.id), "expires_at": made.expires_at.isoformat(), "scopes": made.scopes}

    @router.post(
        "/{lease_id}/cards",
        summary="Add context cards",
        description=(
            "Cards in the card schema only (scope, title, text of at most 600 characters, category, date). "
            "Cards outside the lease's scopes, duplicates and cards holding someone's address or number are "
            "left out and counted. Everything added is private to the owner and ranks lowest."
        ),
    )
    def add_cards(
        lease_id: uuid.UUID, body: CardBatch, person_id: uuid.UUID = Depends(owner_with_leases)
    ) -> dict[str, int]:
        with session_scope() as s:
            try:
                return lease.add_cards(s, require(s, Person, person_id), lease_id, body.cards)
            except lease.LeaseError as exc:
                raise HTTPException(
                    409 if "ended" in str(exc) else 404 if "No such" in str(exc) else 422, str(exc)
                ) from exc

    @router.delete(
        "/{lease_id}",
        summary="End the lease and erase its cards",
        description="Deletes every card that came in under this lease. Nothing the owner told Ava is touched.",
    )
    def end(lease_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_with_leases)) -> dict[str, int]:
        with session_scope() as s:
            try:
                return {"erased": lease.end_lease(s, require(s, Person, person_id), lease_id)}
            except lease.LeaseError as exc:
                raise HTTPException(404, str(exc)) from exc

    return router
