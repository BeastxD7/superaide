"""What Ava may do in the owner's Notion, as the owner's own settings (issue #669, pattern of #433 and #667).

Defaults are conservative. Every limit is enforced in code by the tool; the prompt is never the only copy.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

SKILL_ID = "superaide/notion"
CARD_NAME = "Capabilities → Notion"
CARD_LINK = "/dashboard/capabilities/notion"

CHOICES: dict[str, Choice] = {
    "search_limit": Choice((5, 10, 20), 10, "Pages per search", "Most pages one search may return.", "results"),
    "blocks_read": Choice(
        (20, 50, 100),
        50,
        "Blocks she reads per page",
        "How many paragraphs, headings and list items from the top of one page Ava reads.",
        "blocks",
    ),
    "read_chars": Choice(
        (2000, 6000, 15000),
        6000,
        "Text she reads from one page",
        "Characters of one page that can reach Ava; the rest is cut off.",
        "characters",
    ),
    "reads_per_hour": Choice(
        (10, 30, 100),
        30,
        "Notion lookups per hour",
        "Searches and reads together, so a runaway loop cannot go through your workspace.",
        "lookups",
    ),
    "write_chars": Choice(
        (1000, 3000, 10000),
        3000,
        "Longest draft she may propose",
        "Characters in one new page or comment. You still approve each one first.",
        "characters",
    ),
}  # fmt: skip


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def note(setting: str) -> str:
    label = CHOICES[setting].label
    return f"(limited by the owner's Notion setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"
