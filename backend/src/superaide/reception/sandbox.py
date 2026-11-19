"""Sandbox mode (#499): the owner chats with their own receptionist exactly as a stranger would see it.

One code path: every turn runs `reception.service.handle_stranger`, the function production uses, with a synthetic
unknown sender. What makes it a sandbox is not a different path but a transaction that is never committed:

- Everything the turn writes (the visitor's identity and conversation, the message taken, notes to the owner, triage
  and escalation rows, jobs, audit) is rolled back at the end, so no `reception_message` row, no event and nothing
  counted as real visitor data exists afterwards.
- Everything that would leave the database is stopped in code while `active()`: `notify_owner` records what it would
  have sent instead of sending, the escalation ladder is not started, no channel messenger is reachable, and the
  linking challenge ("I'm <name>") sends no code. The preview shows what would have happened as text.
- The model call is real (inside the spend cap) or the fake, as configured. Its cost is written by the budget guard on
  its own connection, so spend stays visible in the owner's totals, and is returned with the preview.

Safety: a sandbox sender is a fresh unknown account that cannot link to anyone or reach the owner's real contacts,
and the prompt is the receptionist prompt with no extra context, so the preview is an honest view.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from superaide.channels.base import InboundMessage
from superaide.identity.resolver import resolve
from superaide.memory.audience import audience_for
from superaide.memory.store import get_or_create_context, log_event
from superaide.pipeline import Agent
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.reception.sandbox_guard import active, sandboxed

log = logging.getLogger(__name__)

MAX_TURNS = 8  # visitor lines in one preview or replay
MAX_HISTORY = 20  # earlier lines the app sends back so the conversation continues
MAX_TEXT = 1000
SCRIPTS = {
    "meera": {
        "title": "Meera, a first-time visitor",
        "lines": [
            "Hi, who is this?",
            "I'm Meera Iyer. Is Jordan around? I wanted to ask about the design review on Friday.",
            "Could you tell him I'll call at 5pm today, it's fairly urgent?",
            "Thanks, you can reach me on meera@example.com.",
        ],
    }
}


class _Rollback(Exception):
    """Raised at the end of a preview so the session scope rolls everything back."""


class SandboxAgent(Agent):
    """The real agent's model and settings with no channels, and a linking challenge that sends nothing."""

    def __init__(self, real: Any) -> None:
        super().__init__(llm=real.llm, messengers={}, settings=real.settings)
        self.name = real.name

    def _claim(self, s, sender, claimed: str) -> None:
        capture = active()
        if capture is not None:
            capture.skipped.append("linking: no code is sent from a preview")


def standard_scripts() -> list[dict[str, Any]]:
    return [{"id": k, **v} for k, v in SCRIPTS.items()]


def _spent() -> Decimal:
    from superaide.brain.models import SpendEntry

    with session_scope() as s:
        return Decimal(
            s.scalar(select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(SpendEntry.status == "settled")) or 0
        )


def _message(provider: str, sender_id: str, chat: str, text: str, n: int) -> InboundMessage:
    team = "T_SANDBOX" if provider == "slack" else ""
    return InboundMessage(
        provider=provider, tenant_scope=team, sender_id=sender_id, chat_id=chat, chat_kind="dm", text=text,
        verified=True, message_id=f"sandbox-{n}", raw={"team_id": team},
    )  # fmt: skip


def _turn(agent: SandboxAgent, s, sender, ctx, msg: InboundMessage) -> dict[str, Any]:
    """One visitor line through the production function. Returns what the owner is shown for it."""
    from superaide.reception.models import ReceptionMessage
    from superaide.reception.routes import message_view
    from superaide.reception.service import handle_stranger

    inbound = log_event(
        s, context=ctx, direction="in", content=msg.text, audience=audience_for(ctx, sender), sender=sender,
        external_id=msg.message_id,
    )  # fmt: skip
    capture = active()
    assert capture is not None
    before = len(capture.notifications)
    taken_before = set(s.scalars(select(ReceptionMessage.id)))
    mode = "stranger"
    try:
        with s.begin_nested():
            reply = handle_stranger(agent, s, sender, ctx, msg, inbound)
            if reply is None:  # the receptionist is off: the old fixed reply, as a stranger gets it
                reply = agent._ai_notice(agent._stranger(s, sender, msg))
                mode = "fixed"
    except Exception:
        log.exception("sandbox preview turn failed")
        return {"said": msg.text, "reply": None, "mode": "error", "stored": None, "notifications": []}
    stored = None
    for row in s.scalars(select(ReceptionMessage).where(ReceptionMessage.id.notin_(taken_before))):
        stored = message_view(row)
        stored.pop("id", None)
    if reply:
        log_event(s, context=ctx, direction="out", content=reply, audience=audience_for(ctx, sender))
    return {
        "said": msg.text,
        "reply": reply or None,  # "" is silence (over the visitor's limit): shown as no reply
        "mode": mode if reply else "silent",
        "stored": stored,
        "notifications": capture.notifications[before:],
    }


def preview(
    agent: Any, owner_id: uuid.UUID, *, channel: str, history: list[dict[str, str]], messages: list[str]
) -> dict[str, Any]:
    """Run `messages` as a stranger on `channel` after `history` (earlier lines, seeded without a model call), in a
    transaction that is rolled back. Returns each turn, what the settings were, and what it cost."""
    from superaide.identity.models import Person
    from superaide.pipeline import _turn_slots
    from superaide.reception import card
    from superaide.reception.models import ReceptionSettings
    from superaide.reception.service import greeting, settings_for
    from superaide.reception.turn import first_name, has_card, public_facts

    sandbox_agent = SandboxAgent(agent)
    sender_id = f"sandbox-{uuid.uuid4().hex[:12]}"
    spent_before = _spent()
    turns: list[dict[str, Any]] = []
    used: dict[str, Any] = {}
    with _turn_slots(), sandboxed() as capture:
        try:
            with session_scope() as s:
                owner = s.get(Person, owner_id)
                assert owner is not None
                rs: ReceptionSettings = settings_for(s, owner)
                n = 0
                msg0 = _message(channel, sender_id, f"sandbox-chat-{sender_id}", "", n)
                sender = resolve(s, msg0, reconfirm_after_days=sandbox_agent.settings.link_reconfirm_after_days)
                ctx = get_or_create_context(s, msg0)
                aud = audience_for(ctx, sender)
                for item in history[-MAX_HISTORY:]:
                    log_event(
                        s, context=ctx, direction="in" if item["role"] == "visitor" else "out",
                        content=item["text"][:MAX_TEXT], audience=aud,
                        sender=sender if item["role"] == "visitor" else None,
                    )  # fmt: skip
                for text in messages[:MAX_TURNS]:
                    n += 1
                    line = _message(channel, sender_id, msg0.chat_id, text, n)
                    turns.append(_turn(sandbox_agent, s, sender, ctx, line))
                pref = card.preference(s, owner)
                used = {
                    "enabled": rs.enabled,
                    "greeting": greeting(sandbox_agent.name, owner, rs, has_card=has_card(s, owner, rs)),
                    "first_name_shared": bool(rs.show_first_name and first_name(owner)),
                    "profile_used": bool(rs.public_profile.strip()),
                    "card_lines": card.lines(pref),
                    "public_facts": public_facts(s, owner),
                    "per_hour": rs.per_hour,
                    "per_day": rs.per_day,
                }
                raise _Rollback
        except _Rollback:
            pass
    spend = _spent() - spent_before
    with session_scope() as s:  # the only trace: that a preview ran, and what it cost (no words from it)
        audit(s, "reception", "sandbox_preview", turns=len(turns), spend_usd=str(spend), channel=channel)
    return {
        "preview": True,
        "channel": channel,
        "turns": turns,
        "used": used,
        "skipped": capture.skipped,
        "spend_usd": str(spend),
    }
