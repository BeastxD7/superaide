"""Blast-radius cooldown for skill versions (issue #129).

A new version of a skill does not reach installs at once. It waits a period set by the most dangerous effect it
can have, so a hijacked publisher account cannot push a harmful update to everyone at the same moment:

    read_public / read_private   0 hours
    write_own                    24 hours
    write_external, send, spend  72 hours

An earned tier ("reviewed") halves the wait; nothing else shortens it. The one bypass is a security patch signed
by a reviewer: HMAC-SHA256 of "<skill id>@<version>" under SKILL_REVIEWER_KEY (blank key: no bypass exists).
Names of retired skills are reserved for good: they can't be released or installed again.

All of this is decided here, in code. Off unless SKILL_COOLDOWN_ENABLED; with it off nothing changes.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.skills.manifest import Effect
from superaide.skills.models import SkillRelease, SkillTombstone

# Weakest first. A release's effect class is the strongest one it can use.
EFFECT_ORDER: tuple[str, ...] = ("read_public", "read_private", "write_own", "write_external", "send", "spend")
DELAY_HOURS: dict[str, int] = {
    "read_public": 0,
    "read_private": 0,
    "write_own": 24,
    "write_external": 72,
    "send": 72,
    "spend": 72,
}
TIER_FACTOR: dict[str, float] = {"none": 1.0, "reviewed": 0.5}


class CooldownError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return get_settings().skill_cooldown_enabled


def effect_class(effects: list[Effect] | list[str]) -> str:
    """The strongest effect among these (a skill with no tools is read_public)."""
    return max(effects, key=EFFECT_ORDER.index, default="read_public")


def delay(effect: str, earned_tier: str = "none") -> timedelta:
    if effect not in DELAY_HOURS:
        raise CooldownError(422, f"Unknown effect class {effect!r}")
    return timedelta(hours=DELAY_HOURS[effect] * TIER_FACTOR.get(earned_tier, 1.0))


def sign_patch(key: str, skill_id: str, version: str) -> str:
    return hmac.new(key.encode(), f"{skill_id}@{version}".encode(), hashlib.sha256).hexdigest()


def patch_is_signed(rel: SkillRelease) -> bool:
    key = get_settings().skill_reviewer_key
    if not key or not rel.patch_signature:
        return False
    return hmac.compare_digest(rel.patch_signature, sign_patch(key, rel.skill_id, rel.version))


def available_at(rel: SkillRelease) -> datetime:
    return rel.released_at + delay(rel.effect_class, rel.earned_tier)


def is_available(rel: SkillRelease, now: datetime | None = None) -> bool:
    return patch_is_signed(rel) or (now or datetime.now(UTC)) >= available_at(rel)


def is_retired(session: Session, skill_id: str) -> bool:
    return session.scalar(select(SkillTombstone.id).where(SkillTombstone.skill_id == skill_id)) is not None


def retire_name(session: Session, skill_id: str, reason: str = "") -> SkillTombstone:
    """Reserve this name for good. Safe to repeat."""
    row = session.scalar(select(SkillTombstone).where(SkillTombstone.skill_id == skill_id))
    if row is None:
        row = SkillTombstone(skill_id=skill_id, reason=reason[:200], retired_at=datetime.now(UTC))
        session.add(row)
        session.flush()
        audit(session, "skills", "skill_name_retired", skill_id=skill_id)
    return row


def record_release(
    session: Session,
    skill_id: str,
    version: str,
    effects: list[str],
    *,
    earned_tier: str = "none",
    patch_signature: str | None = None,
    now: datetime | None = None,
) -> SkillRelease:
    """Note that this version exists, from now. Repeating it returns the first record: the clock never restarts."""
    if is_retired(session, skill_id):
        raise CooldownError(410, "That skill name was retired and is reserved; it can't be released again")
    if earned_tier not in TIER_FACTOR:
        raise CooldownError(422, "Unknown tier")
    existing = session.scalar(
        select(SkillRelease).where(SkillRelease.skill_id == skill_id, SkillRelease.version == version)
    )
    if existing is not None:
        return existing
    rel = SkillRelease(
        skill_id=skill_id,
        version=version,
        effect_class=effect_class(effects),
        earned_tier=earned_tier,
        released_at=now or datetime.now(UTC),
        patch_signature=patch_signature,
    )
    if patch_signature is not None and not patch_is_signed(rel):
        raise CooldownError(422, "The security-patch signature doesn't verify")
    session.add(rel)
    session.flush()
    audit(
        session, "skills", "skill_release_recorded", skill_id=skill_id, version=version,
        effect_class=rel.effect_class, security_patch=patch_signature is not None,
    )  # fmt: skip
    return rel


def latest_installable(session: Session, skill_id: str, now: datetime | None = None) -> SkillRelease | None:
    """The newest release of this skill that has waited long enough, or None."""
    rels = session.scalars(
        select(SkillRelease).where(SkillRelease.skill_id == skill_id).order_by(SkillRelease.released_at.desc())
    )
    return next((r for r in rels if is_available(r, now)), None)


def guard_install(session: Session, skill_id: str, version: str, now: datetime | None = None) -> None:
    """Refuse to install a retired name, or a version that is still inside its cooldown. No-op while off.
    A version with no release record (a built-in that ships inside the app) is not held back."""
    if not enabled():
        return
    if is_retired(session, skill_id):
        raise CooldownError(410, "That skill name was retired and can't be installed")
    rel = session.scalar(select(SkillRelease).where(SkillRelease.skill_id == skill_id, SkillRelease.version == version))
    if rel is not None and not is_available(rel, now):
        raise CooldownError(
            409, f"Version {version} is new. It becomes available {available_at(rel):%Y-%m-%d %H:%M} UTC."
        )


def view(session: Session, now: datetime | None = None) -> dict[str, list[dict[str, object]]]:
    now = now or datetime.now(UTC)
    releases = session.scalars(select(SkillRelease).order_by(SkillRelease.released_at.desc()))
    return {
        "releases": [
            {
                "skill_id": r.skill_id,
                "version": r.version,
                "effect_class": r.effect_class,
                "released_at": r.released_at.isoformat(),
                "available_at": available_at(r).isoformat(),
                "available": is_available(r, now),
                "security_patch": patch_is_signed(r),
            }
            for r in releases
        ],
        "retired": [
            {"skill_id": t.skill_id, "reason": t.reason, "retired_at": t.retired_at.isoformat()}
            for t in session.scalars(select(SkillTombstone).order_by(SkillTombstone.retired_at.desc()))
        ],
    }
