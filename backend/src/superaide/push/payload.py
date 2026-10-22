"""What a push may say (ADR 0011 section 3.3). Composed here, in code, from structured fields only.

The lock screen is treated as the `public` audience: anyone holding or near the phone, a paired
watch, and apps with notification access can read it. So there is no model call and no free text:
no quotes, amounts, places, meeting names or message text at any preview level. The only words
that can appear besides fixed strings are a sender's first name (at `sender` or `reason`, and only
for people in the owner's People) and a reason category from a fixed list (at `reason`).

`preview_level` per device:
- `none` (the default): "Ava" / "Needs you" (or "3 things need you").
- `sender`: "Ava" / "Priya needs you", only for a known, verified sender; otherwise `none`.
- `reason`: "Ava" / "Priya: deadline today", the category from `REASONS`; never for sensitive topics.

The JSON is padded to a fixed byte length before encryption, so a push's size reveals nothing.
One body serves Safari's declarative web push (RFC 8030 + `web_push: 8030`) and our service worker.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Level = Literal["none", "sender", "reason"]
Kind = Literal["escalation", "digest", "reminder", "approval", "test"]
LEVELS: tuple[str, ...] = ("none", "sender", "reason")

# The only reason text a lock screen may show: fixed categories, never the model's reason line.
REASONS: dict[str, str] = {
    "deadline_today": "deadline today",
    "wants_call": "wants a call",
    "approval_waiting": "approval waiting",
    "schedule_change": "schedule change",
}
# Items touching any of these show the `none` preview whatever the device's level is.
SENSITIVE: frozenset[str] = frozenset(
    {"legal_hr", "payment", "health", "never_share", "credentials", "secrecy", "impersonation"}
)

# Fixed bodies for pushes that aren't escalations (section 4). Content-free at every level.
BODIES: dict[str, str] = {
    "digest": "Your update is ready",
    "reminder": "Reminder",
    "approval": "Something's waiting for your approval",
    "test": "Ava can reach this phone",
}
PATHS: dict[str, str] = {
    "escalation": "/dashboard/needs-you",  # ADR 0010 TR9's page
    "digest": "/dashboard",
    "reminder": "/dashboard/reminders",
    "approval": "/dashboard/approvals",
    "test": "/dashboard",
}
URGENCY: dict[str, str] = {
    "escalation": "high",
    "test": "high",
    "reminder": "normal",
    "digest": "low",
    "approval": "low",
}
ACTIONS: dict[str, list[str]] = {"escalation": ["ack", "snooze30"]}

TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,24}$")
_FIRST_NAME = re.compile(r"^[^\W\d_][^\W\d_'’.-]{0,19}$")


class PayloadTooLarge(ValueError):
    """The composed payload doesn't fit the fixed padded size: a bug, not something to send."""


@dataclass(frozen=True)
class PushItem:
    """What a push is about, as structured fields. Nothing here is free text from a message or the model."""

    kind: Kind
    token: str  # opaque: escalation_delivery.token for escalations, a random one otherwise
    count: int = 1  # items needing the owner (escalations); the badge
    sender_first_name: str = ""  # from the owner's People only
    sender_known: bool = False  # in the owner's People and verified; strangers and unverified are False
    reason: str = ""  # a key of REASONS, or ""
    topics: frozenset[str] = field(default_factory=frozenset)  # risk flags / topic classes of the item

    def __post_init__(self) -> None:
        if not TOKEN.match(self.token):
            raise ValueError("push token must be 8-24 URL-safe characters")


def first_name(name: str) -> str:
    """The first word of a People name, if it looks like a name; otherwise nothing."""
    word = (name or "").strip().split(" ")[0] if name else ""
    return word if _FIRST_NAME.match(word) else ""


def effective_level(level: str, item: PushItem) -> Level:
    """The level actually used: lower than asked when the item or sender doesn't allow more."""
    if item.kind != "escalation" or level not in LEVELS or level == "none":
        return "none"
    if item.topics & SENSITIVE:
        return "none"
    name = first_name(item.sender_first_name) if item.sender_known else ""
    if level == "reason" and item.reason in REASONS:
        return "reason"
    return "sender" if name else "none"


def preview(level: str, item: PushItem) -> tuple[str, str, Level]:
    """(title, body, level used) for this device's preview level."""
    title = "Ava"
    if item.kind != "escalation":
        return title, BODIES[item.kind], "none"
    used = effective_level(level, item)
    name = first_name(item.sender_first_name) if item.sender_known else ""
    if used == "reason":
        category = REASONS[item.reason]
        return title, (f"{name}: {category}" if name else category[:1].upper() + category[1:]), used
    if used == "sender":
        return title, f"{name} needs you", used
    count = max(1, item.count)
    return title, ("Needs you" if count == 1 else f"{count} things need you"), used


def build(item: PushItem, level: str, *, base_url: str, instance: str) -> dict[str, Any]:
    """The JSON body for one device. `instance` is the opaque instance id the service worker sends back."""
    title, body, _ = preview(level, item)
    path = PATHS[item.kind]
    query = f"?d={item.token}" if item.kind == "escalation" else ""
    notification: dict[str, Any] = {
        "title": title,
        "body": body,
        "navigate": f"{base_url.rstrip('/')}{path}{query}",
        "tag": f"{'esc' if item.kind == 'escalation' else item.kind}-{item.token}",
        "silent": False,
    }
    if item.kind == "escalation":
        notification["app_badge"] = max(1, item.count)
    return {
        "web_push": 8030,
        "notification": notification,
        "superaide": {
            "v": 1,
            "instance": instance,
            "token": item.token,
            "kind": item.kind,
            "actions": ACTIONS.get(item.kind, []),
        },
    }


def encode(payload: dict[str, Any], pad_to: int) -> bytes:
    """Compact UTF-8 JSON padded with spaces to exactly `pad_to` bytes (JSON allows trailing space)."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    if len(raw) > pad_to:
        raise PayloadTooLarge(f"push payload is {len(raw)} bytes, more than the fixed {pad_to}")
    return raw + b" " * (pad_to - len(raw))


def headers(item: PushItem, ttl: int) -> dict[str, str]:
    """TTL, Urgency and Topic (a queued push for the same item is replaced, not stacked)."""
    prefix = "esc" if item.kind == "escalation" else item.kind[:3]
    return {"TTL": str(max(0, ttl)), "Urgency": URGENCY[item.kind], "Topic": f"{prefix}-{item.token}"[:32]}
