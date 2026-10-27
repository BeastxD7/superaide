"""How long reminders and approval requests stay open, as the owner's settings (issue #433).

Until now these were server environment variables only. An owner's choice applies when they made one; otherwise the
operator's value (the environment variable) is used, so nothing changes for anyone who has not chosen.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.approvals.binding import APPROVAL_TTL_CHOICES
from superaide.policy.killswitch import get_state
from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

KEY = "settings:retention"
SKILL_ID = f"state:{KEY}"
CARD_NAME = "Settings → Reminders and approvals"

CHOICES: dict[str, Choice] = {
    "reminder_auto_close_days": Choice(
        (0, 3, 7, 14, 30),
        7,
        "Close reminders that went off after",
        "A reminder that went off and was never marked done is closed after this many days. 0 keeps them open.",
        "days",
    ),
    "approval_ttl_hours": Choice(
        APPROVAL_TTL_CHOICES,
        0,
        "Requests that wait for your yes expire after",
        "A request nobody answers can no longer be approved after this many hours, so an old yes cannot run a stale "
        "action. 0 means never. Applies to requests made from now on.",
        "hours",
    ),
}


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def effective(session: Session, name: str, operator_value: int) -> int:
    """The owner's choice if they made one, else the operator's value."""
    if name in (get_state(session, KEY).get("read") or {}):
        return int(load(session)[name])
    return int(operator_value)
