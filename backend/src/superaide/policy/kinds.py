"""What each kind of approval does when the owner says yes (issue #326).

An approval that nothing can carry out is a lie on a card: Ava says "queued", the owner taps yes and
the result is "Nothing can carry this out yet". So every kind is listed here with a one-line,
human description of what happens on approval, and an explicit `executes` marker. A test checks that
the marker matches the real executors (policy/actions.py EXECUTORS), so a new kind cannot be added
without saying what it does.

Code, not the model, decides: `policy.actions.decide(require_executor=True)` refuses a kind with no
executor before any approval row exists, and the refusal text (`unsupported_message`) carries the
facts the model needs to say so plainly and offer what Ava can do instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KindInfo:
    label: str  # short name for the card
    on_approval: str  # one line: what actually happens when the owner approves
    executes: bool  # True: an executor in policy.actions.EXECUTORS carries it out


KINDS: dict[str, KindInfo] = {
    "gmail_send": KindInfo(
        "Send an email from your Gmail",
        "Sends the saved Gmail draft, exactly as shown, from your Gmail.",
        True,
    ),
    "calendar_create_event": KindInfo(
        "Add an event to your calendar", "Adds the event to your Google Calendar, as shown.", True
    ),
    "calendar_move_event": KindInfo(
        "Move a calendar event", "Moves the event in your Google Calendar to the new time.", True
    ),
    "calendar_decline_event": KindInfo(
        "Decline a calendar event", "Declines the invitation in your Google Calendar.", True
    ),
    "ask_reply": KindInfo(
        "Reply to someone from your Ask link",
        "Marks the request answered and gives you the text; you send it yourself (Superaide cannot send it).",
        True,
    ),
    "message_someone_else": KindInfo(
        "Message someone for you",
        "Nothing: Superaide cannot send messages to other people yet (no web forms, contact pages or chat DMs).",
        False,
    ),
    "publish_post": KindInfo("Publish a post", "Nothing: Superaide cannot publish posts yet.", False),
    "grant_access": KindInfo("Grant access", "Nothing: Superaide cannot change anyone's access this way.", False),
    "delete_memory": KindInfo(
        "Delete something she remembers", "Nothing: delete notes yourself under Memory in the app.", False
    ),
    "create_account": KindInfo("Create an account", "Nothing: Superaide cannot create accounts yet.", False),
    "create_profile": KindInfo("Create a profile", "Nothing: Superaide cannot create profiles yet.", False),
    "open_pull_request": KindInfo("Open a pull request", "Nothing: Superaide cannot open pull requests yet.", False),
    "post_review": KindInfo("Post a review", "Nothing: Superaide cannot post reviews yet.", False),
}


def info(kind: str) -> KindInfo:
    return KINDS.get(kind) or KindInfo(kind, "Nothing: no action is wired for this kind.", False)


def on_approval(kind: str) -> str:
    return info(kind).on_approval


def declared_executes(kind: str) -> bool:
    return info(kind).executes


def has_executor(kind: str) -> bool:
    """Whether an executor is registered right now (the truth; `KINDS[...].executes` is its declaration)."""
    from superaide.policy.actions import EXECUTORS, _load_executors

    _load_executors()
    return kind in EXECUTORS


# Routes no executor exists for, whatever the kind: Ava has no way to fill a web form or use a site's
# contact page, so a request that depends on one is refused instead of queued.
_UNSUPPORTED_ROUTE = re.compile(
    r"contact (page|form|us)|web ?form|website form|online form|support (portal|ticket|form)|live ?chat|"
    r"via (their|the) (website|site|portal)|through (their|the) (website|site|portal)|submit (a|the) (form|ticket)",
    re.IGNORECASE,
)


def unsupported_route(text: str) -> bool:
    return bool(_UNSUPPORTED_ROUTE.search(text or ""))


def can_do_list() -> str:
    return (
        "Ava can: save a reply draft in a Gmail thread (gmail_draft_reply), ask the owner to approve sending "
        "a reply in a Gmail thread (gmail_send) or a new email to an address the owner typed (gmail_compose), "
        "both needing Gmail connected with drafts on, write a self-contained "
        "email or message as text for the owner to copy and send themselves, and set reminders."
    )


def unsupported_message(kind: str, summary: str) -> str:
    why = (
        "that depends on a web form or contact page, which Ava cannot use"
        if unsupported_route(summary)
        else f"nothing in Superaide can carry out {kind!r} (approving it would do nothing)"
    )
    return (
        f"Refused: {why}. NO approval was created: do not say anything was queued, sent or waiting. "
        f"Tell the owner plainly that you can't do this yourself, then offer what you can do. {can_do_list()}"
    )
