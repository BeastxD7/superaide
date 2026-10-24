"""/app/passport: take Ava's memory of the owner with you, or bring memory in (docs/memory-passport.md).

Owner only, CSRF-checked, per instance. Export is a zip of the owner's own data. Import is two
steps with the same endpoint: without `confirm` it only validates and says what would change;
with `confirm` it writes, tagged with an import the owner can erase as a set.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.models import MemoryImport, Person
from superaide.db.session import require, session_scope
from superaide.passport import export, importer, schema


def make_passport_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/passport",
        tags=["Owner: memory passport"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/export",
        summary="Download the memory passport",
        description=(
            "A zip of everything Ava knows about the owner: facts, shared files and links, people and preferences, "
            "in the open format described in docs/memory-passport.md. Only the owner's own data; no credentials."
        ),
        response_class=Response,
    )
    def export_passport(
        sources: bool = Query(True, description="Include the text of files and links you shared."),
        person_id: uuid.UUID = Depends(owner_person_id),
    ) -> Response:
        with session_scope() as s:
            data, counts = export.build(s, person_id, include_sources=sources)
        stamp = datetime.now().strftime("%Y-%m-%d")
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="memory-passport-{stamp}.zip"',
                "Cache-Control": "no-store",
                "X-Passport-Facts": str(counts["facts"]),
            },
        )

    @router.post(
        "/import",
        summary="Preview or import a memory passport",
        description=(
            "Upload a passport zip (or a bare facts.jsonl from another assistant). Without `confirm` nothing is "
            "written: the answer says how many facts are new. With `confirm=true` they are added at low confidence, "
            "private to the owner, and never replace what Ava already knows."
        ),
    )
    def import_passport(
        file: UploadFile = File(...),
        confirm: bool = Form(False),
        person_id: uuid.UUID = Depends(owner_person_id),
    ) -> dict[str, Any]:
        raw = file.file.read(schema.MAX_UPLOAD_BYTES + 1)
        try:
            passport = schema.parse(raw)
        except schema.PassportError as exc:
            raise HTTPException(422, str(exc)) from exc
        label = (file.filename or "passport.zip")[:200]
        with session_scope() as s:
            owner = require(s, Person, person_id)
            plan = importer.plan(s, owner, passport)
            summary = importer.preview(plan, passport)
            if not confirm:
                return {"imported": False, **summary}
            if not (plan.new_facts or plan.new_sources or plan.new_people):
                raise HTTPException(409, "Ava already knows everything in this passport.")
            record = importer.apply(s, owner, passport, label=label)
            return {"imported": True, "import_id": str(record.id), **summary}

    @router.get("/imports", summary="Past imports", description="Every passport the owner imported, newest first.")
    def imports(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return {"imports": importer.history(s, person_id)}

    @router.post(
        "/imports/{import_id}/erase",
        summary="Erase an import",
        description="Deletes every fact, file and person that import brought. Nothing else is touched.",
    )
    def erase_import(import_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            record = s.get(MemoryImport, import_id)
            if record is None or record.owner_person_id != person_id or record.erased_at is not None:
                raise HTTPException(404, "Not found")
            return {"erased": importer.erase(s, require(s, Person, person_id), record)}

    return router
