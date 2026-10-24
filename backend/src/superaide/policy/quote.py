"""Quote and Odds (issue #139): honest odds on the approval card, from logged outcomes only.

Every approval that was carried out records `outcome` ("ok" or "failed") in `policy/actions.resolve`. The odds
for a kind are the share of "ok" among those, always shown with the sample size n. Below a minimum sample there
are no odds, only "no history yet". The downgrade to "prepare" is a threshold in code (settings), never a model
opinion. Price: no cost is logged per action yet, so none is quoted. Off unless QUOTE_ODDS_ENABLED.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval

FAILED_PREFIXES = ("Approved, but", "Not sent", "Held:")


def enabled() -> bool:
    return get_settings().quote_odds_enabled


def outcome_of(result: str) -> str:
    return "failed" if result.startswith(FAILED_PREFIXES) else "ok"


def odds(session: Session, kind: str) -> tuple[int, int]:
    """(n, ok): carried-out approvals of this kind with a recorded outcome, and how many worked."""
    col = Approval.payload["outcome"].as_string()
    rows = session.execute(
        select(col, func.count()).where(Approval.kind == kind, col.in_(("ok", "failed"))).group_by(col)
    ).all()
    counts = {k: n for k, n in rows}
    ok = counts.get("ok", 0)
    return ok + counts.get("failed", 0), ok


def assess(session: Session, kind: str) -> dict[str, Any]:
    """Approval-payload fields: the quote, a note for the card and whether to prepare instead of act."""
    if not enabled():
        return {}
    cfg = get_settings()
    n, ok = odds(session, kind)
    if n < cfg.quote_min_samples:
        line = f"No odds yet: only {n} earlier run(s) of this kind are logged (n={n})."
        return {"quote": {"n": n, "ok": ok, "prepare": False}, "notes": [line]}
    rate = ok / n
    prepare = rate < cfg.quote_min_success
    line = f"Worked {ok} of {n} earlier runs of this kind (n={n}, {rate:.0%})."
    if prepare:
        line += " Odds are low, so I will prepare it and you do the last step."
    return {"quote": {"n": n, "ok": ok, "rate": round(rate, 3), "prepare": prepare}, "notes": [line]}


def prepare_only(approval: Approval) -> bool:
    return bool(((approval.payload or {}).get("quote") or {}).get("prepare"))
