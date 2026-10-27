"""Startup list of the modules that plug into a core registry or event (ADR 0015, phase 2).

`policy` does not import the skills that carry out an approved action, and `policy.actions` does not import the
receipts that record a decision: those modules register themselves when they are imported (`EXECUTORS`,
`DISCARDERS`, the event bus). Importing this module imports them all. The composition roots (`main`, `cli`, the
eval harness and the tests' conftest) import it, so every process that can decide an approval has them.

A new capability that registers an executor, a discarder or an event subscriber is added to this list.
"""

from __future__ import annotations

import superaide.asklink.service  # noqa: F401  (EXECUTORS["ask_reply"])
import superaide.channels.slack  # noqa: F401  (MESSENGER_FACTORIES["slack"])
import superaide.channels.telegram  # noqa: F401  (MESSENGER_FACTORIES["telegram"])
import superaide.integrations.calendar.skill  # noqa: F401  (the calendar executors)
import superaide.integrations.gmail.skill  # noqa: F401  (EXECUTORS["gmail_send"], DISCARDERS["gmail_send"])
import superaide.memory.knowledge.connector_facts  # noqa: F401  (subscribes to SkillRemoved, CalendarEventsRead, MailIngested)
import superaide.skills.receipts  # noqa: F401  (subscribes to ApprovalDecided)
import superaide.triage.buttons  # noqa: F401  (PRESS_HANDLERS["telegram"])
