"""The message pipeline: verify, resolve, gate, think, check policy, reply.

Everything that decides who someone is, what they may see and what may run
happens here in code. The model only writes the reply text and proposes
actions; it cannot write identity, trust or memory tables.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError

from superaide.audit import audit
from superaide.brain.budget import AgentFrozen, BudgetExceeded
from superaide.brain.llm import LLM
from superaide.brain.turn import TurnOutput, build_messages, build_system
from superaide.channels.base import InboundMessage, Messenger
from superaide.config import Settings
from superaide.context.audience import Audience, audience_for, visible_memories
from superaide.db.models import Context
from superaide.db.session import session_scope
from superaide.identity import linking
from superaide.identity.resolver import Sender, resolve
from superaide.intentions.when import is_reminder_request
from superaide.knowledge.profile import owner_brief
from superaide.memory.store import (
    get_or_create_context,
    log_event,
    parse_remember,
    recent_events,
    remember,
    seen_before,
)
from superaide.policy.actions import decide
from superaide.policy.killswitch import is_frozen

log = logging.getLogger(__name__)
START_CODE_RE = re.compile(r"^/start\s+([A-Za-z0-9_-]{8,64})\s*$")


def parse_start_code(text: str) -> str | None:
    """Telegram sends "/start <code>" when someone opens t.me/<bot>?start=<code>."""
    m = START_CODE_RE.match(text)
    return m.group(1) if m else None


def thread_of(msg: InboundMessage) -> str:
    """Where the conversation continues: the message's thread, or for an @-mention in a
    Slack channel a new thread under that message (so replies don't flood the channel)."""
    if msg.thread_id:
        return msg.thread_id
    if msg.provider == "slack" and msg.chat_kind == "group":
        return msg.message_id
    return ""


@dataclass
class HandleResult:
    mode: str  # dropped | duplicate | frozen | stranger | known
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
            if seen_before(s, ctx, msg.message_id):
                audit(s, "pipeline", "duplicate_dropped", provider=msg.provider, message_id=msg.message_id[:120])
                return HandleResult("duplicate")
            thread = thread_of(msg)
            try:
                with s.begin_nested():  # two deliveries racing: the unique index lets only one through
                    inbound = log_event(
                        s, context=ctx, direction="in", content=msg.text, audience=aud, sender=sender,
                        external_id=msg.message_id, thread_id=msg.thread_id,
                    )  # fmt: skip
            except IntegrityError:
                return HandleResult("duplicate")

            if is_frozen(s):
                audit(s, "pipeline", "frozen_no_reply", event_id=inbound.id)
                return HandleResult("frozen")

            invite_code = parse_start_code(msg.text) if msg.provider == "telegram" else None
            if invite_code is not None:
                if sender.known:
                    reply = f"You're already linked here, {sender.name}. Just message me."
                else:
                    from superaide.app.accounts import redeem_invite

                    person = redeem_invite(s, provider="telegram", code=invite_code, identity=sender.identity)
                    reply = (
                        f"Linked. Hi {person.display_name}, it's {self.name}. I'll recognise you here from now on."
                        if person
                        else "That link has expired or was already used. Make a new one in the app."
                    )
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("known" if sender.known else "stranger", reply, ["invite"])

            if not sender.known:
                reply = self._stranger(s, sender, msg)
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("stranger", reply)

            if ctx.kind == "dm" and is_reminder_request(msg.text) and self._is_owner(s, sender):
                reply = self._remind(s, sender, inbound)
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("known", reply, ["reminder"])

            fact = parse_remember(msg.text)
            if fact is not None and ctx.kind == "dm":
                remember(s, content=fact, sender=sender, context=ctx, audience=aud, source_event=inbound)
                reply = "Noted. I'll only bring that up with you, in private."
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("known", reply, ["remembered"])

            if ctx.kind == "dm" and self._is_owner(s, sender):
                reply, notes, meta = self._owner_turn(s, sender, ctx, aud, msg, inbound)
                self._reply(s, ctx, aud, reply, thread, meta=meta)
                return HandleResult("known", reply, notes)

            reply, notes = self._think(s, sender, ctx, aud, msg)
            self._reply(s, ctx, aud, reply, thread)
            return HandleResult("known", reply, notes)

    # --- reminders -------------------------------------------------------------

    @staticmethod
    def _is_owner(s, sender: Sender) -> bool:
        from superaide.app.accounts import owner

        boss = owner(s)
        return boss is not None and sender.person is not None and sender.person.id == boss.id

    def _remind(self, s, sender: Sender, inbound) -> str:
        from datetime import UTC, datetime

        from superaide.intentions import service
        from superaide.intentions.when import parse_reminder, zone

        assert sender.person is not None
        tz = service.owner_zone(s, sender.person)
        parsed = parse_reminder(inbound.content, datetime.now(UTC).astimezone(zone(tz)))
        if parsed is None:
            return 'When should I remind you? Say it with a time, like "remind me to call Priya tomorrow at 5".'
        try:
            intention = service.create(
                s, sender.person, text=parsed.task, due_at=parsed.due, source="chat", source_event_id=inbound.id
            )
        except service.IntentionError as exc:
            return str(exc)
        return f"Got it. I'll remind you {service.describe_due(intention.due_at, tz)}: {intention.text}"

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

    def _budget_reply(self, s, exc: BudgetExceeded) -> str:
        audit(s, "pipeline", "budget_cap_reached", period=exc.period)
        if exc.period in {"day", "bucket"}:
            return "I've used today's thinking budget, so I'll pick this up again tomorrow."
        if exc.period == "month":
            return "I've used this month's budget, so I'm pausing until it frees up."
        return f"I've reached my US${self.settings.max_spend_usd:.0f} spending cap, so I'm paused."

    def _owner_turn(
        self, s, sender: Sender, ctx: Context, aud: Audience, msg: InboundMessage, inbound
    ) -> tuple[str, list[str], dict]:
        """The owner's own conversation: a tool loop that can look things up, set reminders,
        search the web and ask for approval, with every answer citing its sources."""
        from superaide.brain.loop import OwnerTurn
        from superaide.factory import uses_real_model

        history = recent_events(s, ctx, thread_id=thread_of(msg))
        turn = OwnerTurn(
            llm=self.llm,
            model=self.settings.model_chat,
            agent_name=self.name,
            session=s,
            sender=sender,
            audience=aud,
            brief=owner_brief(s, sender, aud),
            inbound=inbound,
            web=uses_real_model(self.settings) and self.settings.web_tools_enabled,
        )
        try:
            result = turn.run(build_messages(history, msg.text))
        except BudgetExceeded as exc:
            return self._budget_reply(s, exc), ["budget"], {}
        except AgentFrozen:
            return "I'm paused right now.", ["frozen"], {}
        sources = [
            {"n": i + 1, "uri": c.uri, "kind": c.kind, "title": c.title[:200], "cited_text": c.cited_text[:300]}
            for i, c in enumerate(result.sources)
        ]
        audit(
            s,
            "brain",
            "owner_turn",
            steps=result.steps,
            sources=len(sources),
            tainted=result.tainted,
            notes=result.notes,
        )
        meta = {"sources": sources} if sources else {}
        return result.text, [*result.notes, f"steps:{result.steps}"], meta

    def _think(self, s, sender: Sender, ctx: Context, aud: Audience, msg: InboundMessage) -> tuple[str, list[str]]:
        is_owner = sender.relation == "manager" and aud.person_ids == {sender.person.id} if sender.person else False
        memories = visible_memories(s, aud, limit=30 if is_owner else 5)
        system = build_system(self.name, sender, aud, memories, brief=owner_brief(s, sender, aud))
        messages = build_messages(recent_events(s, ctx, thread_id=thread_of(msg)), msg.text)
        try:
            result = self.llm.complete(
                purpose="chat_turn",
                model=self.settings.model_chat,
                system=system,
                messages=messages,
                max_tokens=4000,  # room for thinking before the reply
                output_model=TurnOutput,
            )
        except BudgetExceeded as exc:
            return self._budget_reply(s, exc), ["budget"]
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

    def _reply(self, s, ctx: Context, aud: Audience, text: str, thread: str = "", meta: dict | None = None) -> None:
        messenger = self.messengers.get(ctx.provider)
        if messenger is None:
            audit(s, "pipeline", "no_messenger", provider=ctx.provider)
            return
        messenger.send_to_context(ctx, text, thread)
        log_event(s, context=ctx, direction="out", content=text, audience=aud, thread_id=thread, meta=meta)
