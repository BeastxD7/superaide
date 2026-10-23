"""The owner's Ava School API (#57), local only. Owner-only, CSRF-checked, per instance; 404 while
SCHOOL_ENABLED is off. Nothing here makes a network call or changes how Ava answers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.app.routes import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.session import session_scope
from superaide.school import checks, document
from superaide.school.models import Lesson
from superaide.tenancy.context import scope_or_default

LIMIT = 200


class LessonBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    role: str = Field(default="personal assistant", min_length=2, max_length=60)


class ConsentBody(BaseModel):
    consent: bool


class ImportBody(BaseModel):
    document: dict[str, Any]


def view(row: Lesson) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "text": row.text,
        "role": row.role,
        "source": row.source,
        "status": row.status,
        "consented": row.consented_at is not None,
        "exported": row.exported_at is not None,
        "created_at": row.created_at.isoformat(),
    }


def problems(items: list[checks.Problem]) -> list[dict[str, str]]:
    return [{"code": p.code, "message": p.message} for p in items]


def refusal(items: list[checks.Problem]) -> HTTPException:
    """422 whose message already says what to change (the app shows `message`), plus the coded problems."""
    why = " ".join(p.message for p in items)
    return HTTPException(
        422, {"message": f"This lesson can't be shared as written. {why}", "problems": problems(items)}
    )


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_agent().settings.school_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/school",
        tags=["Owner: Ava School"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def _mine(s: Any, person_id: uuid.UUID, lesson_id: uuid.UUID) -> Lesson:
        row = s.scalar(
            select(Lesson).where(
                Lesson.id == lesson_id,
                Lesson.owner_person_id == person_id,
                Lesson.agent_id == scope_or_default().agent_id,
            )
        )
        if row is None:
            raise HTTPException(404, "Not found")
        return row

    @router.get("", summary="Your lessons", description="Lessons you wrote, and ones imported and waiting for you.")
    def lessons(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(
                select(Lesson)
                .where(Lesson.owner_person_id == person_id, Lesson.agent_id == scope_or_default().agent_id)
                .order_by(Lesson.created_at.desc())
                .limit(LIMIT)
            ).all()
            return {
                "lessons": [view(r) for r in rows],
                "format": document.FORMAT,
                "note": (
                    "Lessons stay on this device. Exporting makes a file you can read first. "
                    "Nothing is sent anywhere, and Ava does not use a lesson to answer yet."
                ),
            }

    @router.post("/lessons", summary="Write a lesson", description="A generic sentence of advice, never your data.")
    def add(body: LessonBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            text = " ".join(body.text.split())
            found = checks.check(s, person_id, text)
            if found:
                raise refusal(found)
            row = Lesson(owner_person_id=person_id, role=body.role.strip(), text=text, source="own")
            s.add(row)
            s.flush()
            audit(s, "owner", "lesson_added", lesson_id=str(row.id))  # an id, never the words
            return view(row)

    @router.post("/lessons/{lesson_id}/consent", summary="Agree (or stop agreeing) to share a lesson")
    def consent(lesson_id: uuid.UUID, body: ConsentBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            if row.source != "own":
                raise HTTPException(409, "Only lessons you wrote can be shared.")
            if body.consent:
                found = checks.check(s, person_id, row.text)
                if found:
                    raise refusal(found)
            row.consented_at = datetime.now(UTC) if body.consent else None
            audit(s, "owner", "lesson_consent", lesson_id=str(row.id), consent=body.consent)
            return view(row)

    @router.delete("/lessons/{lesson_id}", summary="Delete a lesson")
    def delete(lesson_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            s.delete(row)
            audit(s, "owner", "lesson_deleted", lesson_id=str(lesson_id))
            return {"ok": True}

    @router.post(
        "/lessons/{lesson_id}/accept",
        summary="Accept an imported lesson",
        description="Imported lessons wait here. Accepting keeps one; nothing uses it to answer yet.",
    )
    def accept(lesson_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            if row.source != "imported":
                raise HTTPException(409, "That lesson is already yours.")
            row.status = "active"
            audit(s, "owner", "lesson_accepted", lesson_id=str(row.id))
            return view(row)

    @router.post(
        "/export",
        summary="Export the lessons you agreed to share",
        description=(
            "A file with only their text and role. It is checked again first; any that no longer pass are left "
            "out and listed. A file you have exported can't be taken back, so read it before you share it."
        ),
    )
    def export(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(
                select(Lesson)
                .where(
                    Lesson.owner_person_id == person_id,
                    Lesson.agent_id == scope_or_default().agent_id,
                    Lesson.source == "own",
                    Lesson.consented_at.is_not(None),
                )
                .order_by(Lesson.created_at)
            ).all()
            names, private = checks.known_names(s, person_id), checks._private_shingles(s)
            out: list[dict[str, str]] = []
            skipped: list[dict[str, Any]] = []
            now = datetime.now(UTC)
            for r in rows:
                found = checks.check(s, person_id, r.text, names=names, private=private)
                if found:
                    skipped.append({"id": str(r.id), "problems": problems(found)})
                    continue
                out.append({"text": r.text, "role": r.role})
                r.exported_at = now
            audit(s, "owner", "lessons_exported", count=len(out), skipped=len(skipped))
            return {"document": document.build(out), "count": len(out), "skipped": skipped}

    @router.post(
        "/import",
        summary="Import a lesson file",
        description="Each lesson is checked and waits for you to accept it. Nothing is applied.",
    )
    def import_(body: ImportBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        try:
            doc = document.parse(body.document)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with session_scope() as s:
            have = set(
                s.scalars(
                    select(Lesson.text).where(
                        Lesson.owner_person_id == person_id, Lesson.agent_id == scope_or_default().agent_id
                    )
                )
            )
            names, private = checks.known_names(s, person_id), checks._private_shingles(s)
            added = 0
            rejected: list[dict[str, Any]] = []
            for i, item in enumerate(doc.lessons):
                text = " ".join(item.text.split())
                if text in have:
                    rejected.append(
                        {"index": i, "problems": [{"code": "duplicate", "message": "You already have this lesson."}]}
                    )
                    continue
                found = checks.check(s, person_id, text, names=names, private=private)
                if found:
                    rejected.append({"index": i, "problems": problems(found)})
                    continue
                s.add(Lesson(owner_person_id=person_id, role=item.role.strip() or "personal assistant", text=text,
                             source="imported", status="pending"))  # fmt: skip
                have.add(text)
                added += 1
            audit(s, "owner", "lessons_imported", added=added, rejected=len(rejected))
            return {"added": added, "rejected": rejected}

    return router
