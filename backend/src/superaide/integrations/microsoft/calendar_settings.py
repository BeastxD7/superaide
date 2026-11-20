"""What Ava may do in the owner's Outlook calendar, as the owner's own settings (issue #668, pattern of #433)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/outlook-calendar"
CARD_NAME = "Capabilities → Outlook calendar"
CARD_LINK = "/dashboard/capabilities/outlook-calendar"

CHOICES: dict[str, Choice] = {
    "events_limit": Choice((10, 25, 50), 25, "Events per lookup", "Most events one lookup may return.", "events"),
    "window_days": Choice(
        (7, 14, 30), 14, "How far ahead she looks", "The furthest day from today that Ava can look at.", "days"
    ),
    "reads_per_hour": Choice(
        (10, 30, 100),
        30,
        "Calendar lookups per hour",
        "Event lists and free-time checks together, so a runaway loop cannot go through your calendar.",
        "lookups",
    ),
    "title_chars": Choice(
        (60, 120, 200), 120, "Event title length", "Characters of an event title that can reach Ava.", "characters"
    ),
}  # fmt: skip


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    label = CHOICES[setting].label
    return (
        f"(limited by the owner's Outlook calendar setting “{label}”. "
        f"They can change it under {CARD_NAME}: {CARD_LINK})"
    )
