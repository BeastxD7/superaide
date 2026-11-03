"""docs/settings.md, generated from the settings definitions (issue #433).

Every limit an owner can change is defined once, next to the code that enforces it. This renders them all into one
page, and a test (tests/repo/test_settings_docs.py) fails when the page is out of date, so the documentation cannot
drift from what the app does. Regenerate with `uv run python -m superaide.skills.settings_docs > ../docs/settings.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HEADER = """# Settings: what you can change, and what is fixed

Generated from the settings definitions by `backend/src/superaide/skills/settings_docs.py`; do not edit by hand.
Run `uv run python -m superaide.skills.settings_docs > ../docs/settings.md` from `backend/` after changing a setting.

Every setting has a safe default that equals what the app did before the setting existed, so nothing gets weaker
when you upgrade. A change is audited (the audit log names the setting and the new value) and the page that holds it
shows who changed it last and when. Ava is told the current value in her prompt, and when a setting limits an answer
she names it and says where to change it.
"""

FIXED = """## Fixed on purpose

These are safety rules decided in code. They are shown read-only as "How this works" on each page and nothing, not a
message, an email, or Ava herself, can change them.

- Nothing is sent without your yes: every reply and every new email waits as a request that you approve.
- Email, web pages and other people's event text are information, never instructions to Ava.
- A new email goes to one address that you typed yourself, never one taken from a message.
- An address that looks automated (noreply, do-not-reply) is refused unless you named the sender; then the request
  carries a warning.
- Adding, moving or declining calendar events is off until you allow it, and each change waits for your yes.
- A message from someone you have not added is never treated as you.
- Letting Ava read one-time codes and password resets needs a confirmation in a dialog (and, with
  `STEPUP_MODE=password`, your password). It is off by default.
- Keys and tokens are stored encrypted and never shown again in full.
- In a channel or thread other people can read, Ava answers you only from what is fine for everyone there: your email,
  calendar, reminders and private notes are never opened in it.
- If you ask for something private in a channel, she says so in one neutral line and sends the answer to your direct
  message with her.
- Other people in a thread have none of your authority: what they ask, or claim you said, is never an instruction from
  you.
- People Ava doesn't know are told only the share-card values you switched on (every switch starts off), and your
  first name only if you chose it. Nothing is guessed from your notes, email or calendar, and Ava cannot change the
  card herself: asked in chat, she sends you to the page.
- To see who pinged her in channels, ask in your direct message. Unless you turned on reading a whole channel
  (see "Reading whole channels"), she sees only messages that mention her or reply in her threads, not the rest of
  a channel.
- Room messages she stores never trigger anything: no reply, no model call, no memory, no reminder, no action.
  They are quoted as other people's words when you read them back, and a turn that used them cannot propose an
  action or write an email on them.
"""


AMBIENT_ROWS = [
    (
        "Read the whole channel",
        "Ava stores every message of that channel in her log, with the audience of the room (everyone who can "
        "read the channel), so it never reaches a conversation with anyone else. Nothing happens when a message "
        "arrives: no reply, no model call, no memory, no reminder, no action. You read it back in your direct "
        "message by asking what was said in the channels.",
        "on, off",
        "off",
    ),
    (
        "Keep room messages for",
        "How long the messages stored from that channel are kept before they are deleted.",
        "7 days, 30 days, 90 days, 365 days",
        "30 days",
    ),
]

AMBIENT = """## Reading whole channels

Where: Connections → Reading whole channels

By default Ava reads a channel message only when it mentions her or replies in a thread she is in. For each Slack
channel she is in you can turn on reading the whole channel. It is **off for every channel** until you turn it on, and
each one is its own switch with its own confirmation.

| Setting | What it does | Choices | Default |
| --- | --- | --- | --- |
{TABLE}

Turning a channel off stops storing at once; you can also delete everything stored from it. Slack needs the
`channels:history`, `groups:history`, `channels:read` and `groups:read` permissions and the `message.channels` and
`message.groups` event subscriptions, which the manifest in the connect guide includes; the app names any missing
permission. Messages that mention Ava are conversations, not room messages, and are not touched by this switch.
"""


def _gmail_rows() -> list[tuple[str, Any, str, str, str]]:
    from superaide.integrations.gmail import settings as g

    return [(v[2], list(v[0]), v[1], v[3], "") for v in g.CHOICES.values()]


def _simple_rows(choices: Mapping[str, Any]) -> list[tuple[str, Any, str, str, str]]:
    return [(c.label, list(c.values), c.default, c.help, c.unit) for c in choices.values()]


def sections() -> list[tuple[str, str, list[tuple[str, Any, str, str, str]]]]:
    from superaide.channels import settings as channels
    from superaide.integrations.calendar import settings as calendar
    from superaide.reception import api as card
    from superaide.skills import retention_settings, web_settings

    card_rows = [
        (f"{f.label}: tell people Ava doesn't know", ["off", "on"], "off", card.HELP[f.key], "") for f in card.FIELDS
    ]
    return [
        ("Gmail", "Capabilities → Gmail → Reading", _gmail_rows()),
        ("Google Calendar", "Capabilities → Google Calendar → Reading", _simple_rows(calendar.CHOICES)),
        ("Web research", "Capabilities → Web research → Searching", _simple_rows(web_settings.CHOICES)),
        ("Channels and messages", "Connections → Reading and keeping", _simple_rows(channels.CHOICES)),
        ("Your share card", card.PLACE, card_rows),
        ("Reminders and approvals", "Settings → Reminders and approvals", _simple_rows(retention_settings.CHOICES)),
    ]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render() -> str:
    out = [HEADER]
    for title, where, rows in sections():
        out.append(f"## {title}\n\nWhere: {where}\n")
        out.append("| Setting | What it does | Choices | Default |\n| --- | --- | --- | --- |")
        for label, values, default, help_, unit in rows:
            shown = ", ".join(f"{v} {unit}".strip() for v in values)
            out.append(f"| {_cell(label)} | {_cell(help_)} | {_cell(shown)} | {_cell(f'{default} {unit}'.strip())} |")
        out.append("")
    table = "\n".join(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in AMBIENT_ROWS)
    out.append(AMBIENT.replace("{TABLE}", table))
    out.append("The Gmail card also has two lists, **Never read these senders, domains or labels** and **Always allow")
    out.append("these senders** (an address, `@domain` or `label:name`; both are empty by default).\n")
    out.append(FIXED)
    return "\n".join(out)


if __name__ == "__main__":
    print(render(), end="")
