"""How much Ava reads of conversations and how long she keeps other people's messages, as owner settings (#433).

Defaults are what the app did before. They live in the instance's state store (channels are not a skill), and the
code that reads a Slack thread, trims a cross-channel turn or rolls off old messages asks this module.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.skills.connector_settings import Choice
from superaide.skills.connector_settings import load as load_raw

KEY = "settings:channels"
SKILL_ID = f"state:{KEY}"  # the connector-settings router's name for state-backed settings
CARD_NAME = "Connections → Reading and keeping"
CARD_LINK = "/dashboard/connections"

CHOICES: dict[str, Choice] = {
    "slack_thread_depth": Choice(
        (10, 25, 50),
        25,
        "Slack thread depth",
        "When you @-mention Ava in a Slack thread, how many of its latest messages she reads (other people's words).",
        "messages",
    ),
    "cross_channel_chars": Choice(
        (300, 600, 1200),
        600,
        "Context from your other channels",
        "How much of each earlier message from another channel (Telegram, Slack, the app) Ava is shown as context.",
        "characters",
    ),
    "third_party_retention_days": Choice(
        (0, 30, 90, 365),
        0,
        "Keep other people's messages for",
        "Messages other people sent Ava are deleted after this many days. 0 keeps them until you delete them. "
        "A shorter limit set by the server operator still applies.",
        "days",
    ),
}


def load(session: Session) -> dict[str, Any]:
    return load_raw(session, SKILL_ID, CHOICES)


def retention_days(session: Session, operator_days: int) -> int:
    """The shorter of the owner's choice and the operator's, where 0 means "no limit"."""
    mine = int(load(session)["third_party_retention_days"])
    both = [d for d in (mine, operator_days) if d > 0]
    return min(both) if both else 0
