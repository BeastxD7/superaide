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
    "inbox": "read_private",
    "calendar_events": "read_private",
    "calendar_free_busy": "read_private",
    "gmail_search": "read_private",
    "gmail_read_thread": "read_private",
    "github_search": "read_private",
    "github_read": "read_private",
    "drive_search": "read_private",
    "drive_read": "read_private",
    "web_search": "read_public",
    "web_fetch": "read_public",
    "create_reminder": "write_own",
    "update_reminder": "write_own",
    "cancel_reminder": "write_own",
    "set_presence": "write_own",
    "propose_action": "write_own",  # opens an approval; nothing happens until the owner says yes
    "gmail_draft_reply": "write_external",
    "create_event": "write_external",
    "move_event": "write_external",
    "decline_event": "send",
    "github_create_issue": "write_external",
    "github_comment": "write_external",
    "drive_create_file": "write_external",
    "drive_append_doc": "write_external",
    "gmail_send": "send",
    "gmail_compose": "send",
    "visitor_booking": "write_external",  # opens an approval card; the event is created only after the owner approves
    "relay_message": "send",  # opens an approval card; the message goes only after the owner approves it
}


def effect_of(tool: str) -> str:
    return EFFECTS.get(tool, "send")


def is_read(tool: str) -> bool:
    return effect_of(tool) in READ
