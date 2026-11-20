"""What Ava may do in the owner's Drive, as the owner's own settings (issue #667, pattern of #433).

Defaults are conservative. Every limit is enforced in code by the tool; the prompt is never the only copy.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/google-drive"
CARD_NAME = "Capabilities → Google Drive"
CARD_LINK = "/dashboard/capabilities/drive"

CHOICES: dict[str, Choice] = {
    "search_limit": Choice((5, 10, 20), 10, "Files per search", "Most files one search may return.", "files"),
    "read_chars": Choice(
        (4000, 8000, 20000),
        8000,
        "Text she reads from one file",
        "Characters of one document or sheet that can reach Ava; the rest is cut off.",
        "characters",
    ),
    "reads_per_hour": Choice(
        (10, 30, 100),
        30,
        "Drive lookups per hour",
        "Searches and file reads together, so a runaway loop cannot go through your whole Drive.",
        "lookups",
    ),
    "write_chars": Choice(
        (2000, 5000, 20000),
        5000,
        "Longest draft she may propose",
        "Characters in one new file or one addition. You still approve each one first.",
        "characters",
    ),
}


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    label = CHOICES[setting].label
    return f"(limited by the owner's Drive setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"
