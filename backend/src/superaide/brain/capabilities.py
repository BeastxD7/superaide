"""What Ava can do right now, built from code on every owner turn (issue #328), and a check on action claims.

The model used to guess: "I can't open your Gmail" with no word on how to connect it, "What capabilities do
you have?" with no connection state, and a later "I was wrong earlier" after the owner had simply connected
Gmail in between. Now the state, the exact place to change it and when it last changed come from the database,
and the charter rule below tells her to say "X is connected now", not "I was wrong".

`unsupported_claims` is the other half: a sentence saying she saved a draft, sent, queued or set something
must be backed by a successful tool result in the same turn, or it is removed.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

# Where each thing is turned on, named as the app's own nav shows it (frontend/src/features/dashboard/nav.ts:
# "Capabilities" for skills, "Connections" for chat channels). One place, so the wording cannot drift.
PLACE_GMAIL = "Capabilities → Gmail"
PLACE_CALENDAR = "Capabilities → Google Calendar"

CHANGED_RULE = (
    "When a capability below changed since earlier in this conversation (its 'since' time is after something "
    "you said), say plainly that it is connected or enabled now. Say 'I was wrong' only if you actually stated "
    "something false when you said it."
)


def _since(at: datetime | None, tz: str) -> str:
    if at is None:
        return ""
    from superaide.intentions.when import zone

    return f", since {at.astimezone(zone(tz)):%a %d %b %H:%M}"


def gmail_line(session: Session, tz: str) -> str:
    from superaide.identity.vault.store import stored
    from superaide.integrations.gmail import skill as gmail

    st = gmail.status(session)
    if not st["installed"]:
        return f"Gmail: not added. The owner can add it under {PLACE_GMAIL}."
    if not st["connected"]:
        why = "" if st["client_ready"] else " (a Google sign-in client must be saved first)"
        return f"Gmail: not connected{why}. To connect: {PLACE_GMAIL} → Connect, then approve on Google."
    row = stored(session).get(gmail.SLOT)
    since = _since(getattr(row, "updated_at", None), tz)
    can = "search and read email, and open a thread"
    if not st["drafts"] or not st["drafts_allowed"]:
        return (
            f"Gmail: connected{since}, read-only: you can {can}. You cannot save drafts or ask to send yet. "
            f"To enable: {PLACE_GMAIL} → turn on drafts (Google will ask permission)."
        )
    return (
        f"Gmail: connected{since}, drafts on: you can {can}, and you CAN email people for the owner: reply in a "
        "thread (gmail_send) or write a new email to an address the owner typed or named (gmail_compose). Each "
        "saves the Gmail draft and queues the owner's approval in ONE step; nothing is sent without their yes. "
        "When the owner asks you to send something, call it straight away: do not ask 'want me to request "
        "approval?' and do not use gmail_draft_reply (a draft with no approval) unless they asked for a draft "
        "only. Never say you can't send messages to other people or that Gmail is read-only. You cannot forward."
    )


def calendar_line(session: Session) -> str:
    from superaide.integrations.calendar import skill as calendar

    if not calendar.enabled(session):
        return f"Calendar: not added. The owner can add it under {PLACE_CALENDAR}."
    if not calendar.connected():
        return f"Calendar: added but not connected. To connect: {PLACE_CALENDAR} → Connect."
    return "Calendar: connected: you can read events and free time, and ask the owner to approve changes."


def approvals_line() -> str:
    from superaide.policy.kinds import KINDS

    return "Things the owner can approve: " + "; ".join(
        f"{k.label.lower()} ({k.on_approval.rstrip('.').lower()})" for k in KINDS.values() if k.executes
    )


STANDING_ROWS = 10
STANDING_CHARS = 800
RECENT_HOURS = 48


def standing(session: Session, tz: str) -> str:
    """Pending and recently decided approvals, from the database every turn (issue #340), so the answer to
    "did you send it?" never depends on what an old chat turn said or on which channel it was said on."""
    from datetime import UTC, timedelta

    from sqlalchemy import select

    from superaide.approvals.models import Approval
    from superaide.intentions.when import zone

    now = datetime.now(UTC)
    rows = list(session.scalars(select(Approval).order_by(Approval.created_at.desc()).limit(40)).all())
    keep = [
        a
        for a in rows
        if a.status == "pending" or (a.decided_at is not None and now - a.decided_at < timedelta(hours=RECENT_HOURS))
    ][:STANDING_ROWS]
    if not keep:
        return ""
    out = [
        "Where things stand (from the app's records, authoritative; it overrides anything said earlier in any chat):"
    ]
    for a in keep:
        p = a.payload or {}
        what = str(p.get("summary") or a.kind)[:80].replace("\n", " ")
        if a.status == "pending":
            state = f"WAITING for the owner's OK since {a.created_at.astimezone(zone(tz)):%a %H:%M}, nothing done yet"
        else:
            when = f"{a.decided_at.astimezone(zone(tz)):%a %H:%M}" if a.decided_at else ""
            result = str(p.get("result") or "").replace("\n", " ")[:90]
            state = f"{a.status.upper()} {when}" + (f": {result}" if result else "")
        out.append(f"- {a.kind}: {what} -> {state}")
    text = "\n".join(out)
    return text if len(text) <= STANDING_CHARS else text[: STANDING_CHARS - 1] + "…"


def block(session: Session, *, tz: str, web: bool) -> str:
    lines = [
        "What you can do right now (from the app's own state, authoritative; answer 'what can you do?' from it):",
        "- " + gmail_line(session, tz),
        "- " + calendar_line(session),
        "- Web search: " + ("on." if web else "off (the Web skill is not added)."),
        "- Reminders: always available (create, list, move, cancel).",
        "- " + approvals_line() + ". Anything else cannot be queued; say so and offer text the owner can send.",
        CHANGED_RULE,
    ]
    from superaide.integrations.gmail import settings as gmail_settings
    from superaide.integrations.gmail import skill as gmail

    if gmail.can_read(session):
        lines.append(gmail_settings.rules_text(gmail_settings.load(session)))
    if web:
        from superaide.skills import web_settings

        lines.append(web_settings.rules_text(web_settings.load(session)))
    stand = standing(session, tz)
    return "\n".join(lines) + ("\n\n" + stand if stand else "")


CHANNEL_POLICY = (
    "I don't open your email, calendar or reminder list in a channel, because other people can read it. "
    "Ask me in a direct message and I'll answer there."
)


def shared_block(session: Session, *, web: bool) -> str:
    """The shared-conversation twin of block(): what exists, what is on, and why some of it is not used HERE (#509).

    Names and on/off state only, never a count, a subject or any other data, so it is safe where others can read
    the reply. Without it Ava was told to answer "what can you do?" from a block that was missing, and then said she
    had no Gmail and no capabilities list when the truth was a privacy rule."""
    from superaide.integrations.calendar import skill as calendar
    from superaide.integrations.gmail import skill as gmail

    st = gmail.status(session)
    mail = (
        "connected" + (", drafts on" if gmail.can_draft(session) else ", read-only")
        if st["connected"]
        else "not connected"
        if st["installed"]
        else "not added"
    )
    cal = "connected" if calendar.enabled(session) and calendar.connected() else "not connected"
    return "\n".join(
        [
            "What you can do right now in this shared conversation (from the app's own state, authoritative; "
            "answer 'what can you do?' from it):",
            "- Here, where others can read: recall what is fine for everyone, set a reminder the owner just asked "
            "for, search the web (" + ("on" if web else "off") + "), and queue something for the owner's approval "
            "in the app.",
            f"- Only in a direct message with the owner (never in a channel): Gmail ({mail}), Google Calendar "
            f"({cal}), the reminder list, and the status of approvals. These tools EXIST and may be set up; they "
            "are not used in this room because other people can read your reply.",
            "- When asked about any of them: say it is a channel privacy rule, not a missing connection, and offer "
            "to answer in a direct message. Never say you have no access, no email tool or no capabilities list, "
            "and never suggest the owner's setup is wrong.",
        ]
    )


# --- action claims --------------------------------------------------------------------------------

_CLAIMS: list[tuple[re.Pattern[str], set[str], str]] = [
    (
        re.compile(r"\b(i(?:'ve| have)?\s+(?:just\s+)?(?:saved|created|written)\b[^.\n]{0,30}\bdraft)", re.I),
        {"gmail_draft_reply", "gmail_send", "gmail_compose"},
        "a saved draft",
    ),
    (
        re.compile(
            r"\b(i(?:'ve| have)?\s+(?:just\s+)?sent\b|i sent\b|(?:email|message|it)\s+(?:has been|was) sent)", re.I
        ),
        {"gmail_search", "gmail_read_thread"},  # she may report a send only after looking in Gmail
        "a sent message",
    ),
    (
        re.compile(r"\b(i(?:'ve| have)?\s+(?:just\s+)?(?:set|created|added)\b[^.\n]{0,20}\breminder)", re.I),
        {"create_reminder", "update_reminder"},
        "a reminder",
    ),
    (
        re.compile(r"\b(i(?:'ve| have)?\s+(?:just\s+)?queued\b|queued (?:it |that )?for your approval)", re.I),
        {"propose_action", "gmail_send", "gmail_compose", "create_event", "move_event", "decline_event"},
        "an approval",
    ),
]


def unsupported_claims(text: str, ok_tools: set[str]) -> list[str]:
    """Names of the kinds of action the text claims that no successful tool call this turn backs."""
    return [what for pattern, tools, what in _CLAIMS if pattern.search(text or "") and not (tools & ok_tools)]


_CANT_SEND = re.compile(
    r"\b(can't|cannot|can not|unable to|not able to|am not able to)\s+(send|email|message)\b[^.\n]{0,40}"
    r"\b(other people|anyone|others|people|emails?)\b|\bread-only\b",
    re.IGNORECASE,
)


def contradicts_capability(text: str, can_send: bool) -> bool:
    """With Gmail drafts on she can email people (with approval); "I can't send to other people" or "Gmail is
    read-only" in the same conversation as a saved draft is a contradiction (#394)."""
    return bool(can_send and _CANT_SEND.search(text or ""))


def strip_claims(text: str, ok_tools: set[str], can_send: bool = False) -> tuple[str, list[str]]:
    """Remove the sentences that claim an action nothing did, or deny a capability she has. (text, removed)."""
    removed: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        bad = unsupported_claims(sentence, ok_tools)
        if contradicts_capability(sentence, can_send):
            bad = [*bad, "a statement that you cannot email people"]
        if bad:
            removed += bad
        elif sentence.strip():
            kept.append(sentence.strip())
    if not removed:
        return text, []
    undone = [r for r in dict.fromkeys(removed) if not r.startswith("a statement that")]
    note = "Correction: I haven't actually done that, no tool ran for " + " or ".join(undone) + "." if undone else ""
    if len(undone) < len(set(removed)):
        note = ("Correction: I can email people for you; every send waits for your approval. " + note).strip()
    return "\n".join([*kept, note]), removed


_DENIAL = re.compile(
    r"\b(don't|do not|doesn't|cannot|can't|can not|no|not|isn't|unable)\b[^.\n]{0,60}"
    r"\b(access|have|see|tool|connected|set up|source|list|capabilit\w*)\b",
    re.IGNORECASE,
)
_BLAMES_SETUP = re.compile(r"\bwho(?:ever)? manages your\b|\bask your (?:admin|administrator)\b", re.IGNORECASE)
_PRIVATE_THING = re.compile(r"\b(gmail|e-?mails?|inbox|calendar|capabilit\w*|reminders?)\b", re.IGNORECASE)
_POLICY_ALREADY = re.compile(r"\b(channel|other people|others can|direct message|\bDM\b)\b", re.IGNORECASE)


def strip_false_denials(text: str, *, private_on: bool) -> tuple[str, int]:
    """In a shared conversation, replace "I don't have your Gmail / an email tool / a capabilities list" with the
    real reason (#509). Those words are false there: the owner's mail and calendar exist, they are just not opened
    in a room others can read. Sentences that already give the channel reason are kept. `private_on` is whether
    Gmail or Calendar is actually connected; when neither is, "Gmail isn't connected" is true and stays."""
    kept: list[str] = []
    removed = 0
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        capability_list = re.search(r"capabilit", sentence, re.IGNORECASE) is not None
        denial = _DENIAL.search(sentence) and _PRIVATE_THING.search(sentence) and not _POLICY_ALREADY.search(sentence)
        if _BLAMES_SETUP.search(sentence) or (denial and (private_on or capability_list)):
            removed += 1
        elif sentence.strip():
            kept.append(sentence.strip())
    if not removed:
        return text, 0
    return "\n".join([*kept, CHANNEL_POLICY]), removed
