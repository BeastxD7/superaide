"""The platform-wide daily allowance for calls paid with Superaide's own model key (ADR 0009 T6).

One row per UTC day in `platform_spend` (no agent_id: it belongs to the platform, so
no instance's Row-Level Security hides it). A reservation adds to today's total with
one atomic statement that refuses to pass the cap, so concurrent owners never wait on
a lock and never overshoot together. Settling moves the total by the difference
between the real cost and the reservation; a failed call gives its reservation back.

`PLATFORM_DAILY_CAP_USD` defaults to 0, which refuses every platform-paid call: until
the owner decides to offer a platform key, nothing can spend on one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from superaide.platform.config import get_settings


def _day(at: datetime | None = None) -> date:
    return (at or datetime.now(UTC)).astimezone(UTC).date()


def cap_usd() -> Decimal:
    return Decimal(str(max(0.0, get_settings().platform_daily_cap_usd)))


def draw(session: Session, amount: Decimal) -> bool:
    """Reserve `amount` from today's allowance. False (and nothing changed) if it would pass the cap."""
    cap = cap_usd()
    if amount > cap:
        return False
    row = session.execute(
        text(
            "INSERT INTO platform_spend (day, committed_usd, updated_at) VALUES (:day, :amount, now()) "
            "ON CONFLICT (day) DO UPDATE SET committed_usd = platform_spend.committed_usd + EXCLUDED.committed_usd, "
            "updated_at = now() WHERE platform_spend.committed_usd + EXCLUDED.committed_usd <= :cap "
            "RETURNING committed_usd"
        ),
        {"day": _day(), "amount": amount, "cap": cap},
    ).first()
    return row is not None


def adjust(session: Session, reserved_at: datetime, delta: Decimal) -> None:
    """Move the day's total (the day the reservation was made) by `delta`, never below zero."""
    if delta == 0:
        return
    session.execute(
        text(
            "UPDATE platform_spend SET committed_usd = GREATEST(0, committed_usd + :delta), updated_at = now() "
            "WHERE day = :day"
        ),
        {"day": _day(reserved_at), "delta": delta},
    )


def committed_today(session: Session) -> Decimal:
    value = session.scalar(text("SELECT committed_usd FROM platform_spend WHERE day = :day"), {"day": _day()})
    return Decimal(value or 0)
