"""What Ava may do in the owner's Linear, as the owner's own settings (issue #669, pattern of #433 and #667).

Defaults are conservative. Every limit is enforced in code by the tool; the prompt is never the only copy.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/linear"
CARD_NAME = "Capabilities → Linear"
CARD_LINK = "/dashboard/capabilities/linear"

CHOICES: dict[str, Choice] = {
    "search_limit": Choice((5, 10, 20), 10, "Issues per search", "Most issues one search may return.", "results"),
    "comments_read": Choice(
        (5, 10, 30), 10, "Comments she reads per issue", "The newest comments of an issue that Ava reads.", "comments"
    ),
    "read_chars": Choice(
        (2000, 6000, 15000),
        6000,
        "Text she reads from one issue",
        "Characters of one issue (its description and comments together) that can reach Ava; the rest is cut off.",
        "characters",
    ),
    "reads_per_hour": Choice(
        (10, 30, 100),
        30,
        "Linear lookups per hour",
        "Searches and reads together, so a runaway loop cannot go through your issues.",
        "lookups",
    ),
    "write_chars": Choice(
        (1000, 3000, 10000),
        3000,
        "Longest draft she may propose",
        "Characters in one new issue or comment. You still approve each one first.",
        "characters",
    ),
}  # fmt: skip


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    label = CHOICES[setting].label
    return f"(limited by the owner's Linear setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"
