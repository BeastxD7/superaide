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

# Who may be offered times (#674). Strings, because the setting card shows them as they are.
VISITOR_EVERYONE = "Everyone"
VISITOR_LISTED = "Listed friends and clients"
VISITOR_CLIENTS = "Clients only"
VISITOR_FRIENDS = "Friends only"
# Working days as the number of days open from Monday (Monday to Friday is 5).
WORKING_DAYS: dict[str, int] = {
    "Monday to Thursday": 4,
    "Monday to Friday": 5,
    "Monday to Saturday": 6,
    "Every day": 7,
}
DEFAULT_DAYS = "Monday to Friday"

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
    "visitor_who": Choice(
        (VISITOR_EVERYONE, VISITOR_LISTED, VISITOR_CLIENTS, VISITOR_FRIENDS),
        VISITOR_EVERYONE,
        "Who Ava may offer meeting times to",
        "Everyone: anyone who asks. Listed friends and clients: only accounts you listed under People (and your "
        "colleagues). Clients only, or friends only (people you listed who are not clients): the same, narrower. "
        "Ava decides this from your list, never from what someone says about themselves. Someone outside the group "
        "is told she can't offer times and can leave a message.",
    ),
    "working_days": Choice(
        tuple(WORKING_DAYS),
        DEFAULT_DAYS,
        "Days Ava may offer meeting times on",
        "Visitors are only offered times on these days, inside your day's hours (your time zone).",
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
