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
"""


def _gmail_rows() -> list[tuple[str, Any, str, str, str]]:
    from superaide.integrations.gmail import settings as g

    return [(v[2], list(v[0]), v[1], v[3], "") for v in g.CHOICES.values()]


def _simple_rows(choices: Mapping[str, Any]) -> list[tuple[str, Any, str, str, str]]:
    return [(c.label, list(c.values), c.default, c.help, c.unit) for c in choices.values()]


def sections() -> list[tuple[str, str, list[tuple[str, Any, str, str, str]]]]:
    from superaide.channels import settings as channels
    from superaide.integrations.calendar import settings as calendar
    from superaide.skills import retention_settings, web_settings

    return [
        ("Gmail", "Capabilities → Gmail → Reading", _gmail_rows()),
        ("Google Calendar", "Capabilities → Google Calendar → Reading", _simple_rows(calendar.CHOICES)),
        ("Web research", "Capabilities → Web research → Searching", _simple_rows(web_settings.CHOICES)),
        ("Channels and messages", "Connections → Reading and keeping", _simple_rows(channels.CHOICES)),
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
    out.append("The Gmail card also has two lists, **Never read these senders, domains or labels** and **Always allow")
    out.append("these senders** (an address, `@domain` or `label:name`; both are empty by default).\n")
    out.append(FIXED)
    return "\n".join(out)


if __name__ == "__main__":
    print(render(), end="")
