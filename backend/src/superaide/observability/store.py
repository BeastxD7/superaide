"""Writing and reading turn timings (#412). Failing to record a timing never touches a turn."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.observability.models import TurnTiming
from superaide.observability.timing import STAGES, TurnTimer, log_line
from superaide.platform.db.session import session_scope

log = logging.getLogger("superaide.timing")

QUANTILES = (("p50", 0.5), ("p95", 0.95))


def record(timer: TurnTimer, *, provider: str, mode: str) -> None:
    """One row and one log line for a finished turn. Swallows everything: a metric must not break a reply."""
    try:
        log.info(log_line(timer, provider=provider, mode=mode))
        stages = timer.stage_ms()
        with session_scope() as s:
            s.add(
                TurnTiming(
                    provider=provider[:16],
                    mode=mode[:16],
                    total_ms=timer.total_ms(),
                    model_calls=timer.model_calls,
                    first_token_ms=timer.first_token_ms(),
                    **{f"{name}_ms": stages.get(name) for name in STAGES},
                )
            )
    except Exception:
        log.warning("couldn't record turn timing", exc_info=True)


def _row(s: Session, where: list[Any], group: Any | None) -> list[Any]:
    columns: list[Any] = [func.count().label("turns")]
    for name in ("total", *STAGES, "first_token"):
        col = getattr(TurnTiming, f"{name}_ms")
        columns.append(func.count(col).label(f"{name}_n"))
        for label, q in QUANTILES:
            columns.append(func.percentile_cont(q).within_group(col).label(f"{name}_{label}"))
    stmt = select(*columns).where(*where)
    if group is not None:
        stmt = select(group.label("provider"), *columns).where(*where).group_by(group).order_by(group)
    return list(s.execute(stmt).all())


def _shape(row: Any) -> dict[str, Any]:
    stages: dict[str, dict[str, float | int | None]] = {}
    for name in ("total", *STAGES, "first_token"):
        stages[name] = {
            "n": int(getattr(row, f"{name}_n") or 0),
            **{label: _ms(getattr(row, f"{name}_{label}")) for label, _ in QUANTILES},
        }
    return {"turns": int(row.turns), "stages": stages}


def _ms(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


def summary(s: Session, *, hours: int) -> dict[str, Any]:
    """p50 and p95 in milliseconds for each stage, for all turns and per channel, over the last `hours`.

    Bounded by the (agent_id, created_at) index; at most one row per channel comes back."""
    since = func.now() - func.make_interval(0, 0, 0, 0, hours)
    where = [TurnTiming.created_at > since]
    [overall] = _row(s, where, None)
    return {
        "window_hours": hours,
        "stage_order": list(STAGES),
        "overall": _shape(overall),
        "channels": [{"provider": r.provider, **_shape(r)} for r in _row(s, where, TurnTiming.provider)],
    }
