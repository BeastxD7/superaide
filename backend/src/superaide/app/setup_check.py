"""/app/setup-check: the owner's "Check my setup" button (#567).

Runs the in-app subset of `superaide doctor` (superaide/doctor.py) and returns each result with its cause and
fix. Owner only. A POST with the CSRF header because it makes a request to the configured public address.
Read-only: nothing is changed, and no result contains a secret (they name settings, never values).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from superaide import doctor
from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import require_csrf_header
from superaide.platform.config import get_settings


def make_router() -> APIRouter:
    router = APIRouter(prefix="/app/setup-check", tags=["Setup check"], dependencies=[Depends(require_csrf_header)])

    @router.post(
        "",
        summary="Check my setup",
        description="Runs the checks that matter inside the app (database, key storage, public address, Google "
        "redirects, spend cap) and says how to fix each problem.",
    )
    def run(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        results = doctor.run_in_app(get_settings())
        fails = sum(r.status == doctor.FAIL for r in results)
        warns = sum(r.status == doctor.WARN for r in results)
        return {
            "results": [{"name": r.name, "status": r.status, "cause": r.cause, "fix": r.fix} for r in results],
            "fails": fails,
            "warns": warns,
            "ok": not fails and not warns,
        }

    return router
