"""The agents on the marketplace. Only Ava, the personal assistant, can be hired today."""

from __future__ import annotations

from typing import Any

from superaide.platform.config import Settings


def catalog(settings: Settings) -> list[dict[str, Any]]:
    return [
        {
            "slug": "ava",
            "name": settings.agent_name,
            "role": "Personal assistant",
            "status": "available",
            "hue": "indigo",
            "stage": "Infant",
            "price": "Free while in beta",
            "tagline": "Answers for you when you're busy, and only interrupts you when it really matters.",
            "skills": [
                "Handles people who reach out when you're busy",
                "Knows who matters to you and what counts as urgent",
                "Interrupts you only for what can't wait",
                "Shares only what you said it may share",
                "Keeps a notebook of what it knows, which you can read and edit",
            ],
            "never": [
                "Makes commitments or sends anything as you without your approval",
                "Repeats what you say in private to anyone else",
                "Pretends to be a person",
            ],
            "channels": ["In the app", "Telegram", "Slack"],
        },
        {
            "slug": "leo",
            "name": "Leo",
            "role": "LinkedIn content strategist",
            "status": "coming_soon",
            "hue": "rose",
            "stage": "In training",
            "price": "Coming soon",
            "tagline": "Learns your voice from your edits and drafts posts that sound like you.",
            "skills": [],
            "never": [],
            "channels": [],
        },
        {
            "slug": "kai",
            "name": "Kai",
            "role": "Software developer",
            "status": "coming_soon",
            "hue": "sky",
            "stage": "In training",
            "price": "Coming soon",
            "tagline": "Picks up small tickets, writes tests first and asks before touching anything risky.",
            "skills": [],
            "never": [],
            "channels": [],
        },
    ]


def find(settings: Settings, slug: str) -> dict[str, Any] | None:
    return next((a for a in catalog(settings) if a["slug"] == slug), None)
