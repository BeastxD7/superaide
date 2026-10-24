"""Owner-controlled limits for a capability, shared by Calendar, Web and the rest (issue #433).

Every rule that limits what Ava reads or does in a connector is a setting on that connector's card, with a
plain explanation, a default equal to what the app did before (nothing gets weaker on upgrade), an audit line
per change, and enforcement in code (the tool reads the value; the prompt text is generated from it, never a
separate copy). Gmail has its own richer module (integrations/gmail/settings.py); this one covers the simple
"one value out of a few allowed ones" settings.
"""

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.audit import audit
from superaide.platform.db.models import AuditLog, InstalledSkill
from superaide.platform.db.session import session_scope


@dataclass(frozen=True)
class Choice:
    values: tuple[Any, ...]
    default: Any
    label: str
    help: str
    unit: str = ""  # shown after a number: "days", "emails"


def from_raw(raw: Mapping[str, Any] | None, choices: Mapping[str, Choice]) -> dict[str, Any]:
    """Stored values, each checked against its allowed values; anything else falls back to the default."""
    raw = raw or {}
    out: dict[str, Any] = {}
    for name, c in choices.items():
        value = raw.get(name, c.default)
        out[name] = value if value in c.values else c.default
    return out


def stored(session: Session, skill_id: str) -> InstalledSkill | None:
    return session.scalar(
        select(InstalledSkill).where(InstalledSkill.skill_id == skill_id, InstalledSkill.removed_at.is_(None))
    )


def load(session: Session, skill_id: str, choices: Mapping[str, Choice]) -> dict[str, Any]:
    row = stored(session, skill_id)
    return from_raw(((row.settings if row is not None else None) or {}).get("read"), choices)


def last_changed(
    session: Session, action: str, keys: Callable[[dict[str, Any]], Iterable[str]] | None = None
) -> dict[str, dict[str, str]]:
    """Who last changed each setting and when, from the audit log (every owner change writes one line). Owner
    changes only come through the owner's own session, so `by` is "you". A setting never changed has no entry."""
    which = keys or (lambda detail: detail.get("changed", []))
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.desc()).limit(300)
    )
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        for key in which(row.detail or {}):
            found.setdefault(key, {"by": "you", "at": row.created_at.isoformat()})
    return found


def view(session: Session, skill_id: str, choices: Mapping[str, Choice], event: str | None = None) -> dict[str, Any]:
    return {
        "last_changed": last_changed(session, event) if event else {},
        "settings": load(session, skill_id, choices),
        "choices": {
            k: {"values": list(c.values), "default": c.default, "label": c.label, "help": c.help, "unit": c.unit}
            for k, c in choices.items()
        },
    }


def make_router(
    *, prefix: str, skill_id: str, choices: Mapping[str, Choice], tag: str, what: str, event: str
) -> APIRouter:
    """GET and PUT `/app/<prefix>/read-settings`. PUT takes any subset of the settings."""
    router = APIRouter(
        prefix=f"/app/{prefix}",
        tags=[tag],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    body_model = type(
        f"{prefix.title().replace('-', '')}SettingsBody",
        (BaseModel,),
        {
            "__annotations__": {k: Any for k in choices},
            **{k: None for k in choices},
            "model_config": ConfigDict(extra="forbid"),
        },
    )

    @router.get(
        "/read-settings",
        summary=f"{what}: your settings",
        description=f"The owner's limits for {what}, every allowed value and what it means (defaults until installed).",
    )
    def get_settings(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return view(s, skill_id, choices, event)

    @router.put(
        "/read-settings",
        summary=f"{what}: change your settings",
        description="Changes any of the settings. Each change is audited.",
    )
    def put_settings(body: body_model, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:  # type: ignore[valid-type]
        sent = body.model_dump(exclude_none=True)  # type: ignore[attr-defined]
        with session_scope() as s:
            row = stored(s, skill_id)
            if row is None:
                raise HTTPException(409, "Install this capability first.")
            before = load(s, skill_id, choices)
            now = dict(before)
            for name, value in sent.items():
                if value not in choices[name].values:
                    raise HTTPException(422, f"{name}: choose one of {list(choices[name].values)}")
                now[name] = value
            row.settings = {**(row.settings or {}), "read": now}
            changed = sorted(k for k in now if now[k] != before[k])
            if changed:
                audit(s, "skills", event, changed=changed, values={k: now[k] for k in changed})
            return view(s, skill_id, choices, event)

    return router
