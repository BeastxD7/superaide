"""The message pipeline: verify, resolve, gate, think, check policy, reply.

Everything that decides who someone is, what they may see and what may run
happens here in code. The model only writes the reply text and proposes
actions; it cannot write identity, trust or memory tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agi_agent.audit import audit
from agi_agent.brain.budget import AgentFrozen, BudgetExceeded
from agi_agent.brain.llm import LLM
from agi_agent.brain.turn import TurnOutput, build_messages, build_system
from agi_agent.channels.base import InboundMessage, Messenger
from agi_agent.config import Settings
from agi_agent.context.audience import Audience, audience_for, visible_memories
from agi_agent.db.models import Context
from agi_agent.db.session import session_scope
from agi_agent.identity import linking
from agi_agent.identity.resolver import Sender, resolve
from agi_agent.memory.store import get_or_create_context, log_event, parse_remember, recent_events, remember
from agi_agent.policy.actions import decide
from agi_agent.policy.killswitch import is_frozen

log = logging.getLogger(__name__)


@dataclass
class HandleResult:
    mode: str  # dropped | frozen | stranger | known
    reply: str | None = None
    notes: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, *, llm: LLM, messengers: dict[str, Messenger], settings: Settings) -> None:
        self.llm = llm
        self.messengers = messengers
        self.settings = settings
        self.name = settings.agent_name

    # --- entry point -----------------------------------------------------------

    def handle(self, msg: InboundMessage) -> HandleResult:
        if not msg.verified:
            with session_scope() as s:
                audit(s, "pipeline", "dropped_unverified", provider=msg.provider)
            return HandleResult("dropped")

        with session_scope() as s:
            sender = resolve(s, msg)
            ctx = get_or_create_context(s, msg)
            aud = audience_for(ctx, sender)
            inbound = log_event(s, context=ctx, direction="in", content=msg.text, audience=aud, sender=sender)

            if is_frozen(s):
                audit(s, "pipeline", "frozen_no_reply", event_id=inbound.id)
                return HandleResult("frozen")

            if not sender.known:
                reply = self._stranger(s, sender, msg)
                self._reply(s, ctx, aud, reply)
                return HandleResult("stranger", reply)

            fact = parse_remember(msg.text)
            if fact is not None and ctx.kind == "dm":
                remember(s, content=fact, sender=sender, context=ctx, audience=aud, source_event=inbound)
                reply = "Noted. I'll only bring that up with you, in private."
                self._reply(s, ctx, aud, reply)
                return HandleResult("known", reply, ["remembered"])

            reply, notes = self._think(s, sender, ctx, aud, msg)
            self._reply(s, ctx, aud, reply)
            return HandleResult("known", reply, notes)

    # --- strangers -------------------------------------------------------------

    def _stranger(self, s, sender: Sender, msg: InboundMessage) -> str:
        """No model call, no memory, no actions. The same reply whether or not a
        claimed name exists, so strangers cannot probe who the agent knows."""
        code = linking.detect_code(msg.text)
        if code is not None:
            result = linking.verify_code(
                s,
                identity=sender.identity,
                code=code,
                messengers=self.messengers,
                settings=self.settings,
                agent_name=self.name,
            )
            if result.ok and result.person is not None:
                return f"Linked. Hi {result.person.display_name}, I'll recognise you here from now on."
            return {
                "expired": "That code has expired. Tell me your name again to get a new one.",
                "locked": "Too many wrong codes. Ask again later.",
                "wrong_code": "That code didn't match.",
            }.get(result.reason, "I don't have a pending code for this account.")

        claimed = linking.detect_claimed_name(msg.text)
        if claimed:
            person = linking.find_person_by_name(s, claimed)
            if person is not None:
                linking.start_challenge(
                    s,
                    new_identity=sender.identity,
                    person=person,
                    messengers=self.messengers,
                    settings=self.settings,
                    agent_name=self.name,
                )
            audit(s, "identity", "claim_seen", identity_id=sender.identity.id, claimed=claimed)
            return (
                "Thanks. If we work together, I've sent a code to your usual channel. "
                "Type it here to link this account. Until then I can't share anything or act for you."
            )
        return (
            f"Hi, I'm {self.name}, an AI assistant. I don't recognise this account yet, so I can't help with "
            "work here. If we work together, tell me your name and I'll check with you on your usual channel."
        )

    # --- known people ----------------------------------------------------------

    def _think(self, s, sender: Sender, ctx: Context, aud: Audience, msg: InboundMessage) -> tuple[str, list[str]]:
        memories = visible_memories(s, aud)
        system = build_system(self.name, sender, aud, memories)
        messages = build_messages(recent_events(s, ctx), msg.text)
        try:
            result = self.llm.complete(
                purpose="chat_turn",
                model=self.settings.model_chat,
                system=system,
                messages=messages,
                max_tokens=800,
                output_model=TurnOutput,
            )
        except BudgetExceeded:
            audit(s, "pipeline", "budget_cap_reached")
            return (f"I've reached my US${self.settings.max_spend_usd:.0f} spending cap, so I'm paused.", ["budget"])
        except AgentFrozen:
            return ("I'm paused right now.", ["frozen"])

        out: TurnOutput | None = result.parsed
        if out is None:
            audit(s, "brain", "unparsed_output", text=result.text[:500])
            return ("Sorry, I lost my train of thought. Could you say that again?", ["unparsed"])
        notes = [f"memories:{len(memories)}"]
        audit(
            s,
            "brain",
            "prediction",
            expectation=out.prediction.expectation[:300],
            confidence=out.prediction.confidence,
            cost_usd=float(result.cost_usd),
        )
        reply = out.reply
        if out.requested_action.kind != "none":
            decision = decide(s, kind=out.requested_action.kind, summary=out.requested_action.summary, requester=sender)
            notes.append(f"action:{out.requested_action.kind}:{decision.outcome}")
            if decision.outcome == "refused":
                reply += f"\n\n(I can't do that: {decision.reason}.)"
            elif decision.outcome == "approval":
                reply += "\n\n(Queued for approval. Nothing happens until it's approved.)"
        return reply, notes

    # --- output ------------------------------------------------------------------

    def _reply(self, s, ctx: Context, aud: Audience, text: str) -> None:
        messenger = self.messengers.get(ctx.provider)
        if messenger is None:
            audit(s, "pipeline", "no_messenger", provider=ctx.provider)
            return
        messenger.send_to_context(ctx, text)
        log_event(s, context=ctx, direction="out", content=text, audience=aud)
