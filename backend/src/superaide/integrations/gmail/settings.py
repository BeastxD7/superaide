"""What Ava may read in the owner's Gmail, as the owner's own settings (issue #433).

Every rule that limits what reaches the model used to be a constant nobody could see or change. They are now
settings on the Gmail card (Capabilities → Gmail), stored per owner on the installed skill row, enforced in the
tool results (skills/gmail.py), and described to the model from this same data so it is never told one thing while
the tool does another. When a setting hides something the tool result names it and says where to change it.

Defaults are what the app did before: nothing gets weaker for an existing owner. One-time codes and password
resets stay hidden unless the owner turns them on with their password (a warned, step-up opt-in).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

CARD_LINK = "/dashboard/capabilities#gmail"
CARD_NAME = "Capabilities → Gmail"

# name -> (allowed values, default, label, one-line explanation)
CHOICES: dict[str, tuple[tuple[Any, ...], Any, str, str]] = {
    "payment_preview": (
        ("hide", "show"),
        "hide",
        "Payment and transaction emails: previews",
        "Search results for bank, card and payment mail show only the sender and subject, or also a short preview.",
    ),
    "numbers": (
        ("blanked", "amounts"),
        "blanked",
        "Numbers in emails",
        "Numbers of six digits or more are replaced with [number]. 'amounts' keeps amounts next to a currency "
        "(Rs 1,250.00, $40) and still blanks card, account and code-like numbers.",
    ),
    "codes": (
        ("never", "read"),
        "never",
        "One-time codes and password resets",
        "Mail about one-time codes and password resets is never shown. Turning it on lets a model read secrets "
        "and means a malicious email could try to make Ava repeat them: it needs your password.",
    ),
    "body_chars": (
        (1500, 3000, 6000),
        1500,
        "How much of a message Ava reads",
        "Characters of one message's own text.",
    ),
    "search_limit": ((5, 10, 25), 10, "Search size", "Emails returned by one search."),
    "thread_depth": ((3, 5, 10), 5, "Thread depth", "Latest messages of a thread Ava reads."),
    "lookback_days": ((0, 30, 365), 0, "How far back Ava looks", "Days back; 0 means all of your mail."),
}
LISTS = ("blocked", "allowed")
MAX_ENTRIES = 50
_ENTRY = re.compile(r"^(?:label:[\w./ -]{1,60}|@?[\w.+-]+@[\w.-]+\.\w{2,}|@?[\w.-]+\.\w{2,})$", re.IGNORECASE)


@dataclass
class ReadSettings:
    payment_preview: str = "hide"
    numbers: str = "blanked"
    codes: str = "never"
    body_chars: int = 1500
    search_limit: int = 10
    thread_depth: int = 5
    lookback_days: int = 0
    blocked: list[str] = field(default_factory=list)  # senders, domains and labels Ava never reads
    allowed: list[str] = field(default_factory=list)  # senders whose payment mail Ava may preview and quote

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean_entry(raw: str) -> str:
    entry = " ".join(str(raw).split()).lower()
    return entry if _ENTRY.match(entry) else ""


def from_raw(raw: dict[str, Any] | None) -> ReadSettings:
    """Stored values, each checked against its allowed choices; anything unknown falls back to today's default."""
    raw = raw or {}
    out = ReadSettings()
    for name, (allowed, default, *_rest) in CHOICES.items():
        value = raw.get(name, default)
        setattr(out, name, value if value in allowed else default)
    for name in LISTS:
        entries = [clean_entry(x) for x in (raw.get(name) or []) if isinstance(x, str)]
        setattr(out, name, list(dict.fromkeys(e for e in entries if e))[:MAX_ENTRIES])
    return out


def load(session: Session) -> ReadSettings:
    from superaide.integrations.gmail import skill as gmail

    row = gmail.installed(session)
    return from_raw(((row.settings if row is not None else None) or {}).get("read"))


def matches(entries: list[str], *, address: str, labels: list[str]) -> bool:
    address = (address or "").lower()
    dom = address.rpartition("@")[2]
    have = {x.lower() for x in labels}
    for e in entries:
        if e.startswith("label:"):
            if e[6:].strip().replace(" ", "_") in have or e[6:].strip() in have:
                return True
        elif "@" in e.lstrip("@"):
            if address == e:
                return True
        else:
            d = e.lstrip("@")
            if dom == d or dom.endswith("." + d):
                return True
    return False


def blocked_note(label: str) -> str:
    """What a tool result says when a setting withheld something: which setting, and where to change it."""
    return f"(withheld by the owner's Gmail setting “{label}”. They can change it under {CARD_NAME}: {CARD_LINK})"


def label_of(name: str) -> str:
    return CHOICES[name][2]


def rules_text(s: ReadSettings) -> str:
    """The model's description of the reading rules, from the settings themselves (never a separate copy)."""
    lines = [
        f"Gmail reading rules (the owner's settings under {CARD_NAME}; the tools enforce them):",
        "- Payment and transaction mail: "
        + ("search previews are withheld" if s.payment_preview == "hide" else "previews are shown")
        + "; numbers: "
        + (
            "amounts next to a currency are quoted, long numbers are blanked"
            if s.numbers == "amounts"
            else "numbers of six digits or more are blanked as [number]"
        )
        + ".",
        "- One-time codes and password resets: "
        + (
            "never shown." if s.codes == "never" else "shown (the owner turned this on); never repeat a code to anyone."
        ),
        f"- Reads: {s.search_limit} emails per search, {s.thread_depth} messages per thread, "
        f"{s.body_chars} characters per message, "
        + ("all of the owner's mail." if not s.lookback_days else f"the last {s.lookback_days} days."),
    ]
    if s.blocked:
        lines.append(f"- Never read: {len(s.blocked)} sender, domain or label entries the owner listed.")
    if s.allowed:
        lines.append(f"- Always allowed (previews and amounts): {len(s.allowed)} entries the owner listed.")
    lines.append(
        "When a tool result says something was withheld by a setting, tell the owner which setting and where to "
        f"change it ({CARD_NAME}). Never say you 'can't verify' why, and never claim a limit that is not listed here."
    )
    return "\n".join(lines)
