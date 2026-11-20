"""What Ava may do in the owner's Outlook mail, as the owner's own settings (issue #668, pattern of #433, #667, #669).

Defaults are conservative. Every limit is enforced in code by the tool; the prompt is never the only copy.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/outlook-mail"
CARD_NAME = "Capabilities → Outlook"
CARD_LINK = "/dashboard/capabilities/outlook"

CHOICES: dict[str, Choice] = {
    "search_limit": Choice((5, 10, 20), 10, "Emails per search", "Most emails one search may return.", "emails"),
    "read_chars": Choice(
        (1000, 3000, 8000),
        3000,
        "Text she reads from one email",
        "Characters of one email's own text (no quoted history or signature) that can reach Ava; the rest is cut off.",
        "characters",
    ),
    "reads_per_hour": Choice(
        (10, 30, 100),
        30,
        "Outlook lookups per hour",
        "Searches and reads together, so a runaway loop cannot go through your mailbox.",
        "lookups",
    ),
    "write_chars": Choice(
        (1000, 3000, 8000),
        3000,
        "Longest email she may propose",
        "Characters in one reply or new email. You still approve each one first.",
        "characters",
    ),
    "max_recipients": Choice(
        (1, 3, 5),
        3,
        "Most recipients of one new email",
        "Addresses on one email Ava proposes. A reply goes to its sender only.",
        "people",
    ),
}  # fmt: skip


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    label = CHOICES[setting].label
    return f"(limited by the owner's Outlook setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"
