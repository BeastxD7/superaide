"""The effect class of every tool Ava can call (ADR 0006), kept in one place the replay executor trusts.

A tool not listed here is treated as `send`, the most dangerous class, so replay stubs it. The classes
are the same names the skill manifests use (skills/manifest.py).
"""

from __future__ import annotations

READ = frozenset({"read_public", "read_private"})

EFFECTS: dict[str, str] = {
    "recall": "read_private",
    "list_reminders": "read_private",
    "list_approvals": "read_private",
    "calendar_events": "read_private",
    "calendar_free_busy": "read_private",
    "gmail_search": "read_private",
    "gmail_read_thread": "read_private",
    "web_search": "read_public",
    "web_fetch": "read_public",
    "create_reminder": "write_own",
    "update_reminder": "write_own",
    "cancel_reminder": "write_own",
    "propose_action": "write_own",  # opens an approval; nothing happens until the owner says yes
    "gmail_draft_reply": "write_external",
    "create_event": "write_external",
    "move_event": "write_external",
    "decline_event": "send",
    "gmail_send": "send",
    "gmail_compose": "send",
}


def effect_of(tool: str) -> str:
    return EFFECTS.get(tool, "send")


def is_read(tool: str) -> bool:
    return effect_of(tool) in READ
