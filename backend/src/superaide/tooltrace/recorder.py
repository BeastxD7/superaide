"""Record every tool call as hashes only (issue #110): the name, the effect class, a hash of the
arguments and of the result, whether it failed, and which model and skill versions were involved.

Nothing the owner or anyone else wrote is stored here, so a trace can be shown, diffed and kept without
becoming a second copy of the conversation. Recording never breaks a turn: a failure is logged and skipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from superaide.platform.config import get_settings
from superaide.tooltrace.effects import effect_of
from superaide.tooltrace.models import ToolTrace

log = logging.getLogger("superaide.tooltrace")


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Call:
    """One call as a diff sees it: names and hashes, never content."""

    tool: str
    effect: str
    args_hash: str


def record(
    session: Session,
    *,
    turn_id: uuid.UUID | None,
    seq: int,
    tool: str,
    args: dict[str, Any],
    result: Any,
    is_error: bool,
    model: str,
    skills: dict[str, str] | None = None,
    replay: bool = False,
) -> None:
    if not get_settings().trace_recorder:
        return
    try:
        with session.begin_nested():
            session.add(
                ToolTrace(
                    turn_id=turn_id,
                    seq=seq,
                    tool=tool[:64],
                    effect=effect_of(tool),
                    args_hash=digest(args),
                    result_hash=digest(result),
                    is_error=is_error,
                    model=model[:80],
                    skills=skills or {},
                    replay=replay,
                )
            )
    except Exception:  # a trace must never cost the owner an answer
        log.warning("couldn't record a tool trace", exc_info=True)


def calls_of(session: Session, turn_id: uuid.UUID, *, replay: bool = False) -> list[Call]:
    from sqlalchemy import select

    rows = session.scalars(
        select(ToolTrace).where(ToolTrace.turn_id == turn_id, ToolTrace.replay == replay).order_by(ToolTrace.seq)
    )
    return [Call(r.tool, r.effect, r.args_hash) for r in rows]
