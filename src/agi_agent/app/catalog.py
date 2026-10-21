"""The agents on the marketplace. Only Ava can be hired today."""

from __future__ import annotations

from typing import Any

from agi_agent.config import Settings


def catalog(settings: Settings) -> list[dict[str, Any]]:
    return [
        {
            "slug": "ava",
            "name": settings.agent_name,
            "role": "LinkedIn content strategist",
            "status": "available",
            "hue": "indigo",
            "stage": "Infant",
            "price": "Free while in beta",
            "tagline": "Learns your voice from your edits and drafts posts that sound like you.",
            "skills": [
                "Drafts LinkedIn posts in your voice",
                "Turns your notes and meetings into post ideas",
                "Learns from every edit you make",
                "Keeps a notebook you can read and edit",
            ],
            "never": [
                "Posts, sends or deletes anything without your approval",
                "Repeats what you say in private to anyone else",
                "Pretends to be a person",
            ],
            "channels": ["In the app", "Telegram", "Slack"],
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
        {
            "slug": "rhea",
            "name": "Rhea",
            "role": "Research analyst",
            "status": "coming_soon",
            "hue": "rose",
            "stage": "In training",
            "price": "Coming soon",
            "tagline": "Reads the documents you share and answers with the exact page it came from.",
            "skills": [],
            "never": [],
            "channels": [],
        },
    ]


def find(settings: Settings, slug: str) -> dict[str, Any] | None:
    return next((a for a in catalog(settings) if a["slug"] == slug), None)
