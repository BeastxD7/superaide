"""Which skills exist, which the owner installed, and the tools that gives Ava.

Built-in skills ship in `skills/builtin/` and follow the same protocol as any other skill.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import InstalledSkill
from superaide.skills.manifest import Skill, load_skill

BUILTIN = Path(__file__).with_name("builtin")
WEB_SEARCHES_PER_STEP = 3


class SkillUnavailable(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@lru_cache
def catalog() -> dict[str, Skill]:
    skills = [load_skill(d) for d in sorted(BUILTIN.iterdir()) if d.is_dir()]
    return {s.manifest.id: s for s in skills}


def _rows(session: Session) -> dict[str, InstalledSkill]:
    return {r.skill_id: r for r in session.scalars(select(InstalledSkill))}


def ensure_defaults(session: Session) -> None:
    """Install the default skills once. A default the owner removed stays removed."""
    rows = _rows(session)
    for skill_id, skill in catalog().items():
        if skill.manifest.default_installed and skill_id not in rows:
            session.add(
                InstalledSkill(skill_id=skill_id, version=skill.manifest.version, granted=skill.manifest.permissions)
            )
    session.flush()


def active(session: Session) -> list[Skill]:
    """Installed, enabled skills whose granted permissions still cover what they need."""
    ensure_defaults(session)
    rows, out = _rows(session), []
    for skill_id, skill in catalog().items():
        row = rows.get(skill_id)
        if row and row.enabled and row.removed_at is None and set(skill.manifest.permissions) <= set(row.granted):
            out.append(skill)
    return out


def install(session: Session, skill_id: str, accepted: list[str]) -> InstalledSkill:
    skill = catalog().get(skill_id)
    if skill is None:
        raise SkillUnavailable(404, "No such capability")
    needed = skill.manifest.permissions
    if sorted(set(accepted)) != sorted(needed):
        raise SkillUnavailable(
            409, "The permissions you agreed to don't match what this capability needs. Review them again."
        )
    row = _rows(session).get(skill_id)
    if row is None:
        row = InstalledSkill(skill_id=skill_id, version=skill.manifest.version)
        session.add(row)
    row.version, row.granted, row.enabled, row.removed_at = skill.manifest.version, needed, True, None
    session.flush()
    audit(session, "skills", "skill_installed", skill_id=skill_id, version=row.version, granted=needed)
    return row


def remove(session: Session, skill_id: str) -> None:
    skill = catalog().get(skill_id)
    row = _rows(session).get(skill_id)
    if skill is None or row is None or row.removed_at is not None:
        raise SkillUnavailable(404, "That capability isn't installed")
    if not skill.manifest.removable:
        raise SkillUnavailable(409, f"{skill.manifest.title} is part of how Ava works and can't be removed")
    row.enabled, row.removed_at = False, datetime.now(UTC)
    audit(session, "skills", "skill_removed", skill_id=skill_id)


def option_on(session: Session, skill_id: str, setting: str) -> bool:
    """The owner switched this skill setting on and agreed to what it adds (and the skill is active)."""
    skill = catalog().get(skill_id)
    row = _rows(session).get(skill_id)
    if skill is None or row is None or not row.enabled or row.removed_at is not None:
        return False
    agreed = set(skill.manifest.permissions) | set(skill.manifest.option_permissions(setting))
    return bool((row.settings or {}).get(setting)) and agreed <= set(row.granted)


def options_on(session: Session, skills: list[Skill]) -> set[tuple[str, str]]:
    """(skill id, setting) for every option switched on among these skills."""
    return {
        (skill.manifest.id, setting)
        for skill in skills
        for setting in skill.manifest.options
        if option_on(session, skill.manifest.id, setting)
    }


def set_option(session: Session, skill_id: str, setting: str, *, on: bool, accepted: list[str]) -> InstalledSkill:
    """Switch a skill setting that adds tools on (with the owner's consent to what it adds) or off."""
    skill = catalog().get(skill_id)
    row = _rows(session).get(skill_id)
    if skill is None or row is None or not row.enabled or row.removed_at is not None:
        raise SkillUnavailable(404, "That skill isn't installed")
    if setting not in skill.manifest.options:
        raise SkillUnavailable(404, "No such setting")
    extra = skill.manifest.option_permissions(setting)
    if on and sorted(set(accepted)) != sorted(extra):
        raise SkillUnavailable(409, "The permissions you agreed to don't match what this needs. Review them again.")
    settings = dict(row.settings or {})
    settings[setting] = on
    row.settings = settings
    others = {e for o in skill.manifest.options if o != setting and settings.get(o)
              for e in skill.manifest.option_permissions(o)}  # fmt: skip
    granted = set(row.granted) | set(extra) if on else set(row.granted) - (set(extra) - others)
    order = list(skill.manifest.permissions) + [e for o in skill.manifest.options
                                                for e in skill.manifest.option_permissions(o)]  # fmt: skip
    row.granted = sorted(granted, key=lambda e: order.index(e) if e in order else len(order))
    session.flush()
    audit(session, "skills", "skill_option_on" if on else "skill_option_off", skill_id=skill_id, setting=setting,
          granted=row.granted)  # fmt: skip
    return row


def tools_for(
    skills: list[Skill], *, web_allowed: bool, options: set[tuple[str, str]] | frozenset = frozenset()
) -> list[dict[str, Any]]:
    """Claude tool definitions for these skills. Server tools (web) only when allowed (real model);
    a tool behind a skill setting only while that setting is on (`options`, from options_on)."""
    tools: list[dict[str, Any]] = []
    for skill in skills:
        for tool in skill.manifest.tools:
            if tool.setting is not None and (skill.manifest.id, tool.setting) not in options:
                continue
            if tool.hosted:
                if not web_allowed:
                    continue
                from superaide.brain.providers.anthropic import HOSTED_TYPES

                spec: dict[str, Any] = {"type": HOSTED_TYPES[tool.hosted], "name": tool.name}
                spec["max_uses"] = WEB_SEARCHES_PER_STEP if tool.hosted == "web_search" else 3
                if tool.hosted == "web_fetch":
                    spec["citations"] = {"enabled": True}
                tools.append(spec)
            else:
                tools.append({"name": tool.name, "description": tool.description, "input_schema": tool.input_schema})
    return tools


def instructions_for(skills: list[Skill]) -> str:
    if not skills:
        return ""
    return "Skills you have:\n\n" + "\n\n".join(s.instructions for s in skills)


def view(session: Session) -> list[dict[str, Any]]:
    from superaide.skills.manifest import EFFECT_LABEL

    ensure_defaults(session)
    rows = _rows(session)
    out = []
    for skill_id, skill in catalog().items():
        row = rows.get(skill_id)
        installed = bool(row and row.enabled and row.removed_at is None)
        m = skill.manifest
        out.append(
            {
                "id": skill_id,
                "title": m.title,
                "summary": m.summary,
                "publisher": m.publisher,
                "tier": m.tier,
                "version": m.version,
                "installed": installed,
                "removable": m.removable,
                "permissions": [{"id": p, "label": EFFECT_LABEL[p]} for p in m.permissions],
                "needs_consent": installed and row is not None and not set(m.permissions) <= set(row.granted),
                "tools": [t.name for t in m.tools if t.setting is None],
                "options": [
                    {
                        "setting": o,
                        "title": str(((m.settings or {}).get("properties") or {}).get(o, {}).get("title") or o),
                        "description": str(
                            ((m.settings or {}).get("properties") or {}).get(o, {}).get("description") or ""
                        ),
                        "on": installed and option_on(session, skill_id, o),
                        "permissions": [{"id": p, "label": EFFECT_LABEL[p]} for p in m.option_permissions(o)],
                        "tools": [t.name for t in m.tools if t.setting == o],
                    }
                    for o in m.options
                ],
                "cost": f"About US${m.budget.per_use_usd:.2f} each time it's used"
                if m.budget and m.budget.per_use_usd
                else None,
            }
        )
    return out
