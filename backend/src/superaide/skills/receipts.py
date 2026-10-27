"""The skill receipt log (issue #131): an append-only, hash-chained record of how owners answered a skill's actions.

Each owner's runtime writes one receipt per decision ("approved unedited", "edited", "rejected", "reverted"). A
receipt has no content: a per-skill salted reference for the owner (so the same person cannot be followed from one
skill to another), the skill version, the effect class, the outcome, how long the decision took and whether it was
decided in a batch. Tiers are computed from receipts by `tier_rules` (stdlib only) and anyone can recompute them from
an export with `backend/scripts/skill_tier.py`. Only this module writes the table. Off unless SKILL_RECEIPTS_ENABLED.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from superaide.contracts.events import ApprovalDecided, bus
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval
from superaide.platform.tenancy.context import scope_or_default
from superaide.skills import tier_rules
from superaide.skills.models import SkillReceipt

log = logging.getLogger("superaide.skills.receipts")


def enabled() -> bool:
    return get_settings().skill_receipts_enabled


def rules() -> tier_rules.Rules:
    s = get_settings()
    return tier_rules.Rules(min_days=s.skill_receipt_min_days, owner_cap=s.skill_receipt_owner_cap)


def owner_ref(skill_id: str) -> str:
    """A salted, per-skill pseudonym for this owner. The salt comes from the vault master key and the skill id, so
    the reference is stable inside one skill's log and can't be matched across skills or reversed."""
    from superaide.identity.vault.crypto import master_key

    root, _ = master_key()
    salt = hmac.new(root, b"skill receipt salt v1|" + skill_id.encode(), hashlib.sha256).digest()
    return hmac.new(salt, str(scope_or_default().agent_id).encode(), hashlib.sha256).hexdigest()[:24]


def _row_dict(r: SkillReceipt) -> dict[str, Any]:
    return {
        "seq": r.seq, "prev_hash": r.prev_hash, "entry_hash": r.entry_hash, "skill_id": r.skill_id,
        "version": r.version, "effect_class": r.effect_class, "outcome": r.outcome, "owner_ref": r.owner_ref,
        "ref_seq": r.ref_seq, "latency_s": r.latency_s, "batch": r.batch, "at": r.at.astimezone(UTC).isoformat(),
    }  # fmt: skip


def append(
    session: Session,
    *,
    skill_id: str,
    version: str,
    effect_class: str,
    outcome: str,
    ref_seq: int | None = None,
    latency_s: int = 0,
    batch: bool = False,
    at: datetime | None = None,
) -> SkillReceipt | None:
    if not enabled():
        return None
    if outcome not in tier_rules.OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    scope = scope_or_default()
    # One writer at a time per instance, or two receipts could claim the same predecessor.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"skillreceipt:{scope.agent_id}"}
    )
    last = session.scalar(
        select(SkillReceipt).where(SkillReceipt.agent_id == scope.agent_id).order_by(SkillReceipt.seq.desc())
    )
    row = SkillReceipt(
        seq=(last.seq + 1) if last else 1, prev_hash=last.entry_hash if last else tier_rules.GENESIS,
        entry_hash="", skill_id=skill_id, version=version, effect_class=effect_class, outcome=outcome,
        owner_ref=owner_ref(skill_id), ref_seq=ref_seq, latency_s=max(0, int(latency_s)), batch=batch,
        at=at or datetime.now(UTC),
    )  # fmt: skip
    row.entry_hash = tier_rules.entry_digest(row.prev_hash, tier_rules.fields_of(_row_dict(row)))
    session.add(row)
    session.flush()
    return row


def skill_for_kind(kind: str) -> tuple[str, str, str] | None:
    """(skill id, version, effect class) of the catalog skill that has a tool called `kind`, else None."""
    from superaide.skills import registry

    for skill in registry.catalog().values():
        for tool in skill.manifest.tools:
            if tool.name == kind:
                return skill.manifest.id, skill.manifest.version, tool.effect
    return None


def on_decision(session: Session, approval: Approval, approve: bool) -> None:
    """Called when an approval is decided. Never breaks the decision: a failure is logged and skipped."""
    if not enabled():
        return
    try:
        found = skill_for_kind(approval.kind)
        if found is None:
            return
        payload = approval.payload or {}
        decided = approval.decided_at or datetime.now(UTC)
        with session.begin_nested():
            append(
                session, skill_id=found[0], version=found[1], effect_class=found[2],
                outcome=("edited" if payload.get("edited") else "approved_unedited") if approve else "rejected",
                latency_s=int((decided - approval.created_at).total_seconds()), batch=bool(payload.get("batch")),
                at=decided,
            )  # fmt: skip
    except Exception:
        log.warning("couldn't write a skill receipt", exc_info=True)


bus.subscribe(ApprovalDecided, lambda event: on_decision(event.session, event.approval, event.approved))


def record_revert(session: Session, seq: int) -> SkillReceipt:
    """The owner undid the action behind receipt `seq`. Appends a `reverted` receipt that points at it."""
    target = session.scalar(select(SkillReceipt).where(SkillReceipt.seq == seq))
    if target is None or target.outcome not in ("approved_unedited", "edited"):
        raise LookupError("No approved action with that number")
    row = append(
        session, skill_id=target.skill_id, version=target.version, effect_class=target.effect_class,
        outcome="reverted", ref_seq=target.seq,
    )  # fmt: skip
    assert row is not None
    from superaide.skills import signals

    signals.observe_revert(session, target.skill_id, target.version)
    return row


def rows(session: Session) -> list[dict[str, Any]]:
    scope = scope_or_default()
    q = select(SkillReceipt).where(SkillReceipt.agent_id == scope.agent_id).order_by(SkillReceipt.seq)
    return [_row_dict(r) for r in session.scalars(q)]


def verify(session: Session) -> dict[str, Any]:
    ok, at, why = tier_rules.verify_chain(rows(session))
    return {"ok": ok, "broken_at": at, "reason": why}


def export(session: Session) -> dict[str, Any]:
    """Everything an outside script needs: the rules, the receipts and one Merkle root per day."""
    data = rows(session)
    return {
        "format": "superaide-skill-receipts/1",
        "rules": dataclasses.asdict(rules()),
        "receipts": data,
        "daily_roots": tier_rules.daily_roots(data),
    }


def tiers(session: Session) -> list[dict[str, Any]]:
    return tier_rules.compute_all(rows(session), rules())
