"""An append-only, hash-chained record of what the gateway did (issue #113).

Each entry holds the hash of the one before it, so changing or removing any past entry breaks every hash
after it. Entries carry hashes and names only (tool, effect class, argument and result hashes, approval id),
never content, so erasing someone's data elsewhere leaves the chain intact. Only this module writes the table.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from superaide.config import get_settings
from superaide.db.models import ActionLog
from superaide.tenancy.context import scope_or_default

GENESIS = "0" * 64


def entry_digest(prev_hash: str, fields: dict[str, Any]) -> str:
    body = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _fields(row: ActionLog) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "kind": row.kind,
        "tool": row.tool,
        "effect": row.effect,
        "args_hash": row.args_hash,
        "result_hash": row.result_hash,
        "approval_id": str(row.approval_id) if row.approval_id else None,
        "turn_id": str(row.turn_id) if row.turn_id else None,
        "at": row.at.astimezone(UTC).isoformat(),
    }


def append(
    session: Session,
    *,
    kind: str,
    tool: str,
    effect: str,
    args_hash: str = "",
    result_hash: str = "",
    approval_id: uuid.UUID | None = None,
    turn_id: uuid.UUID | None = None,
) -> ActionLog | None:
    """kind: `call` (the model's tool call ran) or `executed` (an approved action was carried out)."""
    if not get_settings().action_log:
        return None
    scope = scope_or_default()
    # One writer at a time per instance, or two entries could claim the same predecessor.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"actionlog:{scope.agent_id}"})
    last = session.scalar(select(ActionLog).where(ActionLog.agent_id == scope.agent_id).order_by(ActionLog.seq.desc()))
    row = ActionLog(
        seq=(last.seq + 1) if last else 1,
        prev_hash=last.entry_hash if last else GENESIS,
        kind=kind,
        tool=tool[:64],
        effect=effect,
        args_hash=args_hash,
        result_hash=result_hash,
        approval_id=approval_id,
        turn_id=turn_id,
        at=datetime.now(UTC),
        entry_hash="",
    )
    row.entry_hash = entry_digest(row.prev_hash, _fields(row))
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class Verdict:
    ok: bool
    entries: int
    broken_at: int | None = None  # the seq of the first entry that does not fit
    reason: str = ""


def entries_of(session: Session) -> list[ActionLog]:
    scope = scope_or_default()
    return list(session.scalars(select(ActionLog).where(ActionLog.agent_id == scope.agent_id).order_by(ActionLog.seq)))


def verify(session: Session) -> Verdict:
    prev, expected_seq, rows = GENESIS, 1, entries_of(session)
    for row in rows:
        if row.seq != expected_seq:
            return Verdict(False, len(rows), row.seq, "an entry is missing")
        if row.prev_hash != prev:
            return Verdict(False, len(rows), row.seq, "the link to the previous entry is wrong")
        if row.entry_hash != entry_digest(row.prev_hash, _fields(row)):
            return Verdict(False, len(rows), row.seq, "an entry was changed")
        prev, expected_seq = row.entry_hash, expected_seq + 1
    return Verdict(True, len(rows))


def merkle_root(hashes: list[str]) -> str:
    """The root of a binary Merkle tree over the entries' hashes (a lone node is paired with itself)."""
    if not hashes:
        return GENESIS
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256((a + b).encode()).hexdigest() for a, b in zip(level[::2], level[1::2], strict=True)]
    return level[0]


def daily_root(session: Session, day: date) -> str:
    """The root for one UTC day, which the owner can publish or write down and check later."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return merkle_root([r.entry_hash for r in entries_of(session) if start <= r.at < start + timedelta(days=1)])


def count(session: Session) -> int:
    scope = scope_or_default()
    return int(
        session.scalar(select(func.count()).select_from(ActionLog).where(ActionLog.agent_id == scope.agent_id)) or 0
    )
