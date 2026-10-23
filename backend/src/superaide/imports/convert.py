"""Local converters: a third-party export becomes small context cards on the owner's own machine (#133).

Nothing here calls a model or the network. Each converter keeps a title, a short snippet and a date,
and drops what is not the owner's: other people's names in calendar invites (attendees are never
read), the assistant's side of a chat, anything long. The output is the card schema and the owner
reviews it before sending it to a lease.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from superaide.imports.cards import MAX_CARD_TEXT, MAX_CARDS_PER_REQUEST, MAX_TITLE, clean

DEFAULT_SCOPE = {"chat-archive": "chat", "calendar-ics": "calendar", "notes": "notes"}


def _cut(text: str, n: int) -> str:
    text = clean(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def chat_archive(data: Any, scope: str = "chat") -> list[dict[str, Any]]:
    """A conversations.json style archive: one card per conversation, from the owner's first message."""
    cards: list[dict[str, Any]] = []
    for convo in data if isinstance(data, list) else []:
        if not isinstance(convo, dict) or not isinstance(convo.get("mapping"), dict):
            continue
        nodes = [n.get("message") for n in convo["mapping"].values() if isinstance(n, dict)]
        mine = [
            m
            for m in nodes
            if isinstance(m, dict)
            and (m.get("author") or {}).get("role") == "user"
            and isinstance(m.get("content"), dict)
        ]
        mine.sort(key=lambda m: m.get("create_time") or 0)
        first = next(
            (p for m in mine for p in (m["content"].get("parts") or []) if isinstance(p, str) and p.strip()), None
        )
        if first is None:
            continue
        card: dict[str, Any] = {
            "scope": scope,
            "title": _cut(str(convo.get("title") or "Chat"), MAX_TITLE),
            "text": _cut(first, MAX_CARD_TEXT),
            "category": "other",
        }
        stamp = convo.get("create_time")
        if isinstance(stamp, int | float):
            card["when"] = datetime.fromtimestamp(stamp, UTC).date().isoformat()
        cards.append(card)
    return cards[:MAX_CARDS_PER_REQUEST]


_ICS_FIELD = re.compile(r"^(SUMMARY|DTSTART)[^:]*:(.*)$")


def calendar_ics(text: str, scope: str = "calendar") -> list[dict[str, Any]]:
    """A calendar .ics: title and date of each event. Attendees, locations and descriptions are not read."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    cards: list[dict[str, Any]] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, flags=re.S):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            m = _ICS_FIELD.match(line.strip())
            if m:
                fields.setdefault(m.group(1), m.group(2).strip())
        title = _cut(fields.get("SUMMARY", "").replace("\\,", ",").replace("\\n", " "), MAX_TITLE)
        if not title:
            continue
        card: dict[str, Any] = {"scope": scope, "title": "", "text": f"Calendar event: {title}", "category": "other"}
        d = re.match(r"(\d{4})(\d{2})(\d{2})", fields.get("DTSTART", ""))
        if d:
            card["when"] = f"{d.group(1)}-{d.group(2)}-{d.group(3)}"
        cards.append(card)
    return cards[:MAX_CARDS_PER_REQUEST]


def notes(text: str, scope: str = "notes") -> list[dict[str, Any]]:
    """Plain text or Markdown notes: one card per paragraph, long paragraphs cut."""
    paras = [p for p in re.split(r"\n\s*\n", text) if clean(p)]
    return [
        {"scope": scope, "title": "", "text": _cut(p, MAX_CARD_TEXT), "category": "other"}
        for p in paras[:MAX_CARDS_PER_REQUEST]
    ]


def convert(kind: str, text: str, *, scope: str = "") -> dict[str, Any]:
    scope = scope or DEFAULT_SCOPE[kind]
    if kind == "chat-archive":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("That file is not a JSON chat archive") from exc
        cards = chat_archive(data, scope)
    elif kind == "calendar-ics":
        cards = calendar_ics(text, scope)
    else:
        cards = notes(text, scope)
    return {"cards": cards}
