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

from superaide.app.deps import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.brain.budget import AgentFrozen, BudgetExceeded
from superaide.db.session import session_scope
from superaide.policy.killswitch import get_state, set_state
from superaide.school import checks, document, playbook, signing
from superaide.school.models import Lesson
from superaide.tenancy.context import scope_or_default

LIMIT = 200
SHARE_KEY = "school_share"
MAX_CATEGORIES, MAX_TRUSTED = 20, 50


class SharingBody(BaseModel):
    categories: list[str] = Field(max_length=MAX_CATEGORIES)  # lesson roles that may leave; empty means none
    trusted_keys: list[str] | None = Field(default=None, max_length=MAX_TRUSTED)


def _norm(cat: str) -> str:
    return " ".join(cat.lower().split())


def sharing_of(s: Any) -> dict[str, Any]:
    st = get_state(s, SHARE_KEY)
    return {"categories": list(st.get("categories") or []), "trusted_keys": list(st.get("trusted_keys") or [])}


class LessonBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    role: str = Field(default="personal assistant", min_length=2, max_length=60)


class ConsentBody(BaseModel):
    consent: bool


class PlaybookBody(BaseModel):
    text: str = Field(min_length=1, max_length=playbook.MAX_PLAYBOOK)


class EvalBody(BaseModel):
    spec: dict[str, Any] | None = None  # {say, expect: [...]} or a one-turn scenario
    yaml: str | None = Field(default=None, max_length=4000)  # the same, as in evals/scenarios/*.yaml


class SwitchBody(BaseModel):
    on: bool


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
        "playbook_text": row.playbook_text,
        "eval_spec": row.eval_spec,
        "eval_status": row.eval_status,
        "eval_report": row.eval_report,
        "eval_at": row.eval_at.isoformat() if row.eval_at else None,
        "in_prompt": row.in_prompt,
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

    def sharing_on() -> bool:
        return bool(get_agent().settings.school_sharing_enabled)

    def signer() -> signing.Signer:
        try:
            return signing.signer_for(get_agent().settings.school_signer)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

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
                    "Nothing is sent anywhere. Ava reads a lesson only if you wrote playbook text for it, its eval "
                    "passed and you switched it on."
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

    @router.get(
        "/sharing",
        summary="Which lesson categories may be shared",
        description="Default none. Only lessons whose category (role) you allow here go into a signed file.",
    )
    def sharing(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        if not sharing_on():
            raise HTTPException(404, "Not Found")
        with session_scope() as s:
            mine = [r for (r,) in s.execute(select(Lesson.role).where(Lesson.owner_person_id == person_id).distinct())]
            pub = signer().public_key()
            return {
                **sharing_of(s),
                "available_categories": sorted(mine),
                "signer": {"alg": signer().alg, "public_key": pub, "key_id": signing.key_id(pub)},
            }

    @router.put("/sharing", summary="Choose which lesson categories may be shared")
    def set_sharing(body: SharingBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        if not sharing_on():
            raise HTTPException(404, "Not Found")
        with session_scope() as s:
            cur = sharing_of(s)
            cats = sorted({_norm(c) for c in body.categories if c.strip()})
            cur["categories"] = cats
            if body.trusted_keys is not None:
                cur["trusted_keys"] = sorted({k.strip() for k in body.trusted_keys if k.strip()})
            set_state(s, SHARE_KEY, cur)
            audit(s, "owner", "school_sharing_set", categories=len(cats), trusted=len(cur["trusted_keys"]))
            return cur

    async def playbook_on(person_id: uuid.UUID = Depends(owner_on)) -> uuid.UUID:
        if not get_agent().settings.school_playbook_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    @router.put(
        "/lessons/{lesson_id}/playbook",
        summary="Write the playbook text for a lesson",
        description="Your own words, used only after the lesson's eval passed and you switched it on. "
        "Changing the text drops any earlier pass.",
    )
    def put_playbook(
        lesson_id: uuid.UUID, body: PlaybookBody, person_id: uuid.UUID = Depends(playbook_on)
    ) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            playbook.set_playbook(row, body.text)
            audit(s, "owner", "lesson_playbook_set", lesson_id=str(row.id))
            return view(row)

    @router.put(
        "/lessons/{lesson_id}/eval",
        summary="Attach an eval to a lesson",
        description="A scenario-shaped turn: what the owner says (`say`) and reply checks (`expect`), as in "
        "backend/evals/scenarios. Changing it drops any earlier pass.",
    )
    def put_eval(lesson_id: uuid.UUID, body: EvalBody, person_id: uuid.UUID = Depends(playbook_on)) -> dict[str, Any]:
        raw = body.spec if body.spec is not None else body.yaml
        try:
            spec = playbook.parse_spec(raw)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            playbook.set_spec(row, spec)
            audit(s, "owner", "lesson_eval_set", lesson_id=str(row.id))
            return view(row)

    @router.post(
        "/lessons/{lesson_id}/eval/run",
        summary="Run a lesson's eval",
        description="Two short model calls, inside the spend cap: without the playbook and with it. It passes only "
        "if the answer with it meets every check and the answer without it does not.",
    )
    def run_eval(lesson_id: uuid.UUID, person_id: uuid.UUID = Depends(playbook_on)) -> dict[str, Any]:
        agent = get_agent()
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            try:
                playbook.run_eval(s, row, llm=agent.llm, model=agent.settings.model_chat, agent_name=agent.name)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            except (BudgetExceeded, AgentFrozen) as exc:
                raise HTTPException(402, "I can't run this right now (spend limit or paused).") from exc
            audit(s, "owner", "lesson_eval_run", lesson_id=str(row.id), status=row.eval_status)
            return view(row)

    @router.post(
        "/lessons/{lesson_id}/in-prompt",
        summary="Switch a lesson on or off in Ava's prompt",
        description="Only after its eval passed on the current text. Off any time.",
    )
    def in_prompt(
        lesson_id: uuid.UUID, body: SwitchBody, person_id: uuid.UUID = Depends(playbook_on)
    ) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, lesson_id)
            try:
                playbook.switch(row, body.on)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            audit(s, "owner", "lesson_in_prompt", lesson_id=str(row.id), on=body.on)
            return view(row)

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
            held_back = 0
            allowed = {_norm(c) for c in sharing_of(s)["categories"]}
            now = datetime.now(UTC)
            for r in rows:
                if sharing_on() and _norm(r.role) not in allowed:
                    held_back += 1  # consented, but its category isn't one the owner allows to leave
                    continue
                found = checks.check(s, person_id, r.text, names=names, private=private)
                if found:
                    skipped.append({"id": str(r.id), "problems": problems(found)})
                    continue
                out.append({"text": r.text, "role": r.role})
                r.exported_at = now
            audit(s, "owner", "lessons_exported", count=len(out), skipped=len(skipped), held_back=held_back)
            if sharing_on():
                doc = signing.sign_document(signer(), document.SIGNED_FORMAT, out)
            else:
                doc = document.build(out)
            return {"document": doc, "count": len(out), "skipped": skipped, "held_back": held_back}

    @router.post(
        "/import",
        summary="Import a lesson file",
        description="Each lesson is checked and waits for you to accept it. Nothing is applied.",
    )
    def import_(body: ImportBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        signer_info: dict[str, Any] | None = None
        doc: document.LessonFile | document.SignedLessonFile
        try:
            if sharing_on():
                doc, pub = document.parse_signed(body.document, signer().verify)
                signer_info = {"key_id": signing.key_id(pub), "alg": doc.signer.get("alg", "")}
            else:
                doc = document.parse(body.document)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with session_scope() as s:
            if signer_info is not None:
                trusted = sharing_of(s)["trusted_keys"]
                known = {signing.key_id(k) for k in trusted} | set(trusted)
                signer_info["trusted"] = signer_info["key_id"] in known
                if trusted and not signer_info["trusted"]:
                    raise HTTPException(422, "This file is signed by someone you haven't trusted, so it isn't read.")
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
            return {"added": added, "rejected": rejected, **({"signer": signer_info} if signer_info else {})}

    return router
