"""What Ava may read of the owner's calendar, as the owner's own settings (issue #433).

Defaults are what the app did before: nothing changes for an existing owner until they choose.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/google-calendar"
CARD_NAME = "Capabilities → Google Calendar"
CARD_LINK = "/dashboard/capabilities#calendar"

CHOICES: dict[str, Choice] = {
    "ahead_days": Choice(
        (7, 14, 30), 14, "How far ahead Ava reads", "Days of your calendar one question can cover.", "days"
    ),
    "day_start": Choice(
        (6, 7, 8, 9, 10), 8, "Your day starts at", "Free time is described from this hour (24h clock)."
    ),
    "day_end": Choice(
        (17, 18, 19, 20, 21, 22), 20, "Your day ends at", "Free time is described up to this hour (24h clock)."
    ),
    "events_per_read": Choice(
        (5, 10, 20),
        10,
        "Events she learns from per read",
        "Events from one read that Ava may turn into notes about you (only when learning from connectors is on).",
        "events",
    ),
    "visitor_slots": Choice(
        (0, 2, 3),
        3,
        "Meeting times Ava may offer visitors",
        "How many times Ava offers someone she doesn't know who asks to meet you (0 turns this off). Only free times "
        "inside your day are offered, never what is in your calendar, and nothing is booked until you approve it.",
        "times",
    ),
    "buffer_minutes": Choice(
        (0, 15, 30),
        15,
        "Gap kept around your events",
        "Minutes kept clear before and after anything in your calendar when Ava offers a visitor a time.",
        "minutes",
    ),
}


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    """What a tool result says when a setting limited the answer: which one, and where to change it."""
    label = CHOICES[setting].label
    return f"(limited by the owner's calendar setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"
