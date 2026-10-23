"""The message pipeline: verify, resolve, gate, think, check policy, reply.

Everything that decides who someone is, what they may see and what may run
happens here in code. The model only writes the reply text and proposes
actions; it cannot write identity, trust or memory tables.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import cast

from sqlalchemy.exc import IntegrityError

from superaide.audit import audit
from superaide.brain.budget import AgentFrozen, BudgetExceeded
from superaide.brain.llm import LLM
from superaide.brain.llm import OnEvent as Emit
from superaide.brain.turn import TurnOutput, build_messages, build_system
from superaide.channels.base import InboundMessage, Messenger
from superaide.channels.format import strip_citations
from superaide.channels.health import DeliveryFailed
from superaide.config import Settings, get_settings
from superaide.context.audience import Audience, audience_for, visible_memories
from superaide.db.models import Context, Person
from superaide.db.session import session_scope
from superaide.identity import linking
from superaide.identity.resolver import Sender, resolve
from superaide.intentions.when import is_reminder_request
from superaide.knowledge import trust
from superaide.knowledge.profile import owner_brief
from superaide.memory.store import (
    get_or_create_context,
    log_event,
    owner_timeline,
    parse_remember,
    recent_events,
    remember,
    seen_before,
    strip_label,
)
from superaide.policy.actions import decide
from superaide.policy.killswitch import is_frozen

log = logging.getLogger(__name__)
START_CODE_RE = re.compile(r"^/start\s+([A-Za-z0-9_-]{8,64})\s*$")


def parse_start_code(text: str) -> str | None:
    """Telegram sends "/start <code>" when someone opens t.me/<bot>?start=<code>."""
    m = START_CODE_RE.match(text)
    return m.group(1) if m else None


# "I need to …", "I have to …", "I must …", "I've got to …", "to …": something to do.
OBLIGATION = re.compile(
    r"^\s*(?:i\s+(?:really\s+)?(?:need|have|want|must|should|ought)(?:\s+to)?|i(?:'ve|\s+have)\s+got\s+to|i\s+gotta|to)\s+",
    re.I,
)


def thread_of(msg: InboundMessage) -> str:
    """Where the conversation continues: the message's thread, or for an @-mention in a
    Slack channel a new thread under that message (so replies don't flood the channel)."""
    if msg.thread_id:
        return msg.thread_id
    if msg.provider == "slack" and msg.chat_kind == "group":
        return msg.message_id
    return ""


SET_PREFIX = "Got it. I'll remind you "


def reminder_meta(reply: str) -> dict | None:
    """The code-only reminder reader's reply, recorded as the tool it stands in for, so the next
    turn's "[Tools used …]" line shows the reminder really was set (brain/turn.py tools_used)."""
    if not reply.startswith(SET_PREFIX):
        return None  # it asked when, or the time had passed: nothing was set
    when, _, task = reply[len(SET_PREFIX) :].partition(": ")
    activity = {
        "kind": "tool",
        "name": "create_reminder",
        "state": "done",
        "label": f"Setting a reminder: {task}, {when}",
        "detail": f"Reminder set: {task}, {when}.",
    }
    return {"activity": [activity]}


def max_concurrent_turns() -> int:
    settings = get_settings()
    return max(1, (settings.db_pool_size + settings.db_max_overflow) // 2 - 1)


@lru_cache
def _turn_slots() -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(max_concurrent_turns())


@dataclass
class HandleResult:
    mode: str  # dropped | duplicate | frozen | halted | stranger | unconfirmed | known
    reply: str | None = None
    notes: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, *, llm: LLM, messengers: dict[str, Messenger], settings: Settings) -> None:
        self.llm = llm
        self.messengers = messengers
        self.settings = settings
        self.name = settings.agent_name

    # --- entry point -----------------------------------------------------------

    def handle(self, msg: InboundMessage, emit: Emit | None = None) -> HandleResult:
        """`emit`, when given, receives live progress of an owner turn (the web chat streams it).

        Only `max_concurrent_turns()` turns run at once: each holds a database connection for the whole
        turn and the spend guard takes a second one for every model call, so more turns than connections
        would all wait for each other until the pool timed out (issue #84)."""
        with _turn_slots():
            return self._handle(msg, emit)

    def _handle(self, msg: InboundMessage, emit: Emit | None = None) -> HandleResult:
        if not msg.verified:
            with session_scope() as s:
                audit(s, "pipeline", "dropped_unverified", provider=msg.provider)
            return HandleResult("dropped")

        with session_scope() as s:
            sender = resolve(s, msg, reconfirm_after_days=self.settings.link_reconfirm_after_days)
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

            if self.settings.time_travel_rules_enabled and not self._is_owner(s, sender):
                try:  # shadow bookkeeping only: it never changes the reply or acts
                    with s.begin_nested():
                        from superaide.rules.timetravel import record_firing

                        record_firing(s, inbound, provider=msg.provider, sender=sender.name if sender.known else "")
                except Exception:
                    log.exception("couldn't record standing-order firings")

            if sender.known and not sender.unconfirmed and (sender.relation == "manager" or self._is_owner(s, sender)):
                from superaide.policy.breaker import halt, is_halt_word

                if is_halt_word(msg.text):  # before any model sees it, and even when already frozen
                    reply = halt(s, via=msg.provider)
                    self._reply(s, ctx, aud, reply, thread)
                    return HandleResult("halted", reply)

            if is_frozen(s):
                audit(s, "pipeline", "frozen_no_reply", event_id=inbound.id)
                return HandleResult("frozen")

            invite_code = parse_start_code(msg.text) if msg.provider == "telegram" else None
            if invite_code is not None:
                if sender.known:
                    reply = f"You're already linked here, {sender.name}. Just message me."
                else:
                    from superaide.app.accounts import redeem_invite

                    person = redeem_invite(
                        s, provider="telegram", code=invite_code, identity=sender.identity, endpoint=msg.endpoint
                    )
                    if person is None:
                        reply = self._ai_notice("That link has expired or was already used. Make a new one in the app.")
                    elif sender.unconfirmed:
                        reply = f"Confirmed. Hi {person.display_name}, it's {self.name}. This bot is yours now."
                    else:
                        reply = (
                            f"Linked. Hi {person.display_name}, it's {self.name}. I'll recognise you here from now on."
                        )
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("known" if sender.known else "stranger", reply, ["invite"])

            if sender.unconfirmed:
                # A linked account on a bot (or app) it wasn't confirmed on, or back after a long
                # silence. Whoever holds that account now isn't trusted with anything private until
                # the owner confirms in the signed-in app: a fixed reply, no model, no memory.
                self._tell_owner_unconfirmed(s, sender, msg)
                if ctx.kind != "dm":
                    return HandleResult("unconfirmed")
                reply = self._unconfirmed_reply(sender, msg.provider)
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("unconfirmed", reply)

            if not sender.known:
                from superaide.identity import claims

                if claims.enabled():
                    # An unknown account that is named like someone Ava knows is still a stranger; the owner is
                    # asked who they are (issue #93). A blocked account gets no reply at all.
                    if claims.is_blocked(s, sender.identity):
                        audit(s, "identity", "blocked_sender_ignored", identity_id=sender.identity.id)
                        return HandleResult("stranger", None, ["blocked"])
                    if ctx.kind == "dm":
                        try:
                            with s.begin_nested():
                                claims.review(s, sender.identity, msg)
                        except Exception:  # the neutral reply still goes out, as for every stranger
                            log.warning("claim review failed", exc_info=True)
                # Receptionist mode (ADR 0010 TR5): a real conversation on the owner's behalf, from public
                # information only. None: off, not a DM, or a linking code, so the fixed reply as before.
                from superaide.reception.service import handle_stranger

                try:
                    with s.begin_nested():
                        received = handle_stranger(self, s, sender, ctx, msg, inbound)
                except Exception as exc:  # the fixed reply still goes out (issue #83)
                    self._turn_failed(s, exc, msg)
                    received = None
                if received == "":
                    return HandleResult("stranger", None, ["rate_limited"])
                reply = received if received is not None else self._ai_notice(self._stranger(s, sender, msg))
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("stranger", reply, ["receptionist"] if received is not None else [])

            # With a model connected, Ava handles reminders herself (her create_reminder tool), so
            # she understands typos and phrasing and you can watch her reason. The code-only
            # reader below is for when no model is connected.
            ava_decides = ctx.kind == "dm" and self._is_owner(s, sender) and self._brain_ready()
            if ctx.kind == "dm" and is_reminder_request(msg.text) and self._is_owner(s, sender) and not ava_decides:
                reply = self._remind(s, sender, inbound)
                self._reply(s, ctx, aud, reply, thread, meta=reminder_meta(reply))
                return HandleResult("known", reply, ["reminder"])

            fact = parse_remember(msg.text)
            if fact is not None and ctx.kind == "dm" and not (ava_decides and OBLIGATION.match(fact)):
                # "Remember that I need to go to the gym at 12pm" means a reminder, not a note.
                if self._is_owner(s, sender) and (set_reminder := self._remind_from_fact(s, sender, inbound, fact)):
                    self._reply(s, ctx, aud, set_reminder, thread, meta=reminder_meta(set_reminder))
                    return HandleResult("known", set_reminder, ["reminder"])
                rank = trust.OWNER_VERIFIED if self._is_owner(s, sender) else trust.THIRD_PARTY
                remember(
                    s, content=fact, sender=sender, context=ctx, audience=aud, source_event=inbound, trust_rank=rank
                )
                # Honest about what happened, without sending the private text back over a channel.
                reply = "Saved to your notebook. Only you can see it."
                self._reply(s, ctx, aud, reply, thread)
                return HandleResult("known", reply, ["remembered"])

            if self.settings.relationship_safety_enabled and ctx.kind == "dm" and self._is_owner(s, sender):
                from superaide.trust import companion

                if (
                    steady := companion.route(s, cast(Person, sender.person), msg.text)
                ) is not None:  # fixed script, no model call
                    self._reply(s, ctx, aud, steady, thread)
                    return HandleResult("known", steady, ["steady_voice"])

            if ctx.kind == "dm" and self._is_owner(s, sender):
                try:
                    with s.begin_nested():  # a failed turn undoes its own half-done work, not the inbound record
                        reply, notes, meta = self._owner_turn(s, sender, ctx, aud, msg, inbound, emit)
                except Exception as exc:
                    reply, notes, meta = self._turn_failed(s, exc, msg), ["failed"], {"failed": type(exc).__name__}
                reply = self._steady(s, reply)
                self._reply(s, ctx, aud, reply, thread, meta=meta)
                from superaide.knowledge import learn

                learn.schedule(s, inbound)  # after the reply: learn anything worth keeping
                from superaide.journal import service as journal

                journal.schedule(s, inbound)  # only if the owner turned the decision journal on
                return HandleResult("known", reply, notes)

            if self.settings.triage_enabled:  # ADR 0010, shadow until TR5: the reply below is unchanged
                self._shadow_triage(s, sender, inbound)
            try:
                with s.begin_nested():
                    reply, notes = self._think(s, sender, ctx, aud, msg)
            except Exception as exc:
                reply, notes = self._turn_failed(s, exc, msg), ["failed"]
            reply = self._steady(s, reply)
            outsider = sender.relation != "manager" and not self._is_owner(s, sender)  # the owner never gets the card
            self._reply(s, ctx, aud, reply, thread, to=(sender.name if sender.known else "") if outsider else None)
            return HandleResult("known", reply, notes)

    @staticmethod
    def _turn_failed(s, exc: Exception, msg: InboundMessage) -> str:
        """Anything the model phase raises (provider overload or timeout, a tool error) ends in a short
        apology instead of silence, a 500, or a rolled-back inbound message (issue #83)."""
        log.error("turn failed on %s: %s", msg.provider, type(exc).__name__, exc_info=exc)
        audit(s, "pipeline", "turn_failed", provider=msg.provider, error=type(exc).__name__)
        return "I couldn't think that through just now. Please try again in a moment."

    def _steady(self, s, text: str) -> str:
        """Whatever the model wrote, Ava claims no love or feelings (RELATIONSHIP_SAFETY_ENABLED, issue #124)."""
        if not self.settings.relationship_safety_enabled:
            return text
        from superaide.trust.companion import filter_output

        if (clean := filter_output(text)) != text:
            audit(s, "safety", "feelings_claim_filtered")
            return clean
        return text

    def _shadow_triage(self, s, sender: Sender, inbound) -> None:
        """Queue a recorded triage decision for a known non-owner's message. A failure here never
        touches the reply."""
        if self._is_owner(s, sender):
            return
        try:
            with s.begin_nested():
                from superaide.triage.service import schedule

                schedule(s, inbound)
        except Exception:
            log.exception("couldn't queue triage")

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
        return f"{SET_PREFIX}{service.describe_due(intention.due_at, tz)}: {intention.text}"

    def _brain_ready(self) -> bool:
        from superaide.factory import brain_ready

        return brain_ready(self.settings)

    def _remind_from_fact(self, s, sender: Sender, inbound, fact: str) -> str | None:
        """A reminder if the fact names a moment; None if it's just something to know."""
        from datetime import UTC, datetime

        from superaide.intentions import service
        from superaide.intentions.when import clean_task, find_moment, zone

        assert sender.person is not None
        duty = OBLIGATION.match(fact)
        if duty is None:
            return None  # "my flight is on Friday" is something to know, not something to do
        tz = service.owner_zone(s, sender.person)
        found = find_moment(fact[duty.end() :], datetime.now(UTC).astimezone(zone(tz)))
        if found is None:
            return None
        due, rest = found
        task = clean_task(rest)
        if not task:
            return None
        try:
            intention = service.create(
                s, sender.person, text=task[:1].upper() + task[1:], due_at=due, source="chat",
                source_event_id=inbound.id,
            )  # fmt: skip
        except service.IntentionError as exc:
            return str(exc)
        return f"{SET_PREFIX}{service.describe_due(intention.due_at, tz)}: {intention.text}"

    # --- linked accounts on an unconfirmed endpoint ------------------------------

    def _unconfirmed_reply(self, sender: Sender, provider: str) -> str:
        where, what = ("Slack", "app") if provider == "slack" else ("Telegram", "bot")
        steps = f"open Superaide → Connections → {where} → Confirm this {what}."
        if sender.unconfirmed == "stale":
            return f"It's been a while. To keep your chats private, confirm it's you: {steps}"
        return f"This is a new {self.name} {what}. To keep your chats private, confirm it's you: {steps}"

    def _endpoint_name(self, provider: str, endpoint: str) -> str:
        if provider == "telegram":
            from superaide.channels.telegram import bot_id

            if endpoint and endpoint == bot_id(self.settings.telegram_bot_token):
                from superaide.app.routes import telegram_username

                username = telegram_username(self.settings.telegram_bot_token)
                if username:
                    return f"bot @{username}"
            return f"bot (id {endpoint})" if endpoint else "bot"
        return "Slack app"

    def _tell_owner_unconfirmed(self, s, sender: Sender, msg: InboundMessage) -> None:
        """Once per new bot (or silence): an in-app notice. Never pushed to a chat channel."""
        if not sender.first_notice:
            return
        from superaide.app.accounts import owner
        from superaide.proactive.notify import notify_owner

        boss = owner(s)
        if boss is None or sender.linked_person is None:
            return
        whose = "your" if sender.linked_person.id == boss.id else f"{sender.linked_person.display_name}'s"
        where = "Slack" if msg.provider == "slack" else "Telegram"
        if sender.unconfirmed == "stale":
            text = f"{whose.capitalize()} {where} account messaged me after a long silence."
        else:
            on = self._endpoint_name(msg.provider, msg.endpoint)
            text = f"A message came from {whose} {where} account on the new {on}."
        text += f" Confirm it's you in Connections → {where}. If it wasn't you, unlink that account there."
        notify_owner(
            s, boss, text,
            {"kind": "security", "event": "link_unconfirmed", "identity_id": str(sender.identity.id)},
            channel="app",
        )  # fmt: skip
        audit(s, "identity", "owner_notified_unconfirmed", identity_id=sender.identity.id, reason=sender.unconfirmed)

    # --- strangers -------------------------------------------------------------

    def _ai_notice(self, reply: str) -> str:
        """Anyone Ava doesn't know is told they're talking to an AI, in every reply (EU AI Act Art. 50)."""
        if "AI assistant" in reply:
            return reply
        return f"(I'm {self.name}, an AI assistant.) {reply}"

    def _claim(self, s, sender: Sender, claimed: str) -> None:
        """Someone unknown said a name: if it's someone we know, send a code to their linked channel.
        Whatever the answer, the caller replies the same way, so nobody can probe who Ava knows."""
        from superaide.identity import claims

        person = linking.find_person_by_name(s, claimed, exact=claims.enabled())
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
            self._claim(s, sender, claimed)
            return (
                "Thanks. If we work together and you've already linked another channel, I'll send a code there; "
                "type it here to link this account. Setting me up for the first time? Open the link from "
                "Connections in the app instead. Until then I can't share anything or act for you."
            )
        return (
            f"Hi, I'm {self.name}, an AI assistant. I don't recognise this account yet, so I can't help with "
            "work here. If we work together, open the link from Connections in the app, or tell me your name "
            "and I'll check with you on a channel you've already linked."
        )

    # --- known people ----------------------------------------------------------

    def _key_rejected(self, s, exc: Exception, *, owner: bool) -> str | None:
        """The provider refused the key (or Superaide's credits): say so plainly to the owner, give others a
        plain "can't answer", and tell the owner in the app once per three hours. None: not that kind of error."""
        from superaide.brain import keyhealth

        if not keyhealth.is_auth_error(exc):
            return None
        provider = keyhealth.provider_of(exc)
        keyhealth.record_rejected(provider)
        audit(s, "pipeline", "ai_key_rejected", provider=provider)
        if not owner:
            from superaide.app.accounts import owner as hired_by

            boss = hired_by(s)
            if boss is not None:
                keyhealth.note_owner(s, boss, provider)
        return keyhealth.message_for(provider) if owner else keyhealth.OTHERS_CHAT

    def _budget_reply(self, s, exc: BudgetExceeded, *, owner: bool = True) -> str:
        """What to say when a spending limit stops a model call. The owner hears which limit and what to
        do about it; anyone else gets a neutral line, because they can't act on it and it would show
        how the owner pays for the assistant."""
        audit(s, "pipeline", "budget_cap_reached", period=exc.period, audience="owner" if owner else "other")
        if not owner:
            return "Sorry, I can't help with this right now. Please try again a little later."
        if exc.period == "platform_day":
            return "You've used today's Superaide credits. Add your own AI key on the AI model page, or try tomorrow."
        if exc.period == "platform":
            return (
                "Everyone's used up today's shared Superaide credits. "
                "Add your own AI key on the AI model page, or try tomorrow."
            )
        if exc.period == "platform_month":
            return "You've used this month's Superaide credits. Add your own AI key on the AI model page to carry on."
        if exc.period in {"day", "bucket"}:
            return "I've used today's thinking budget, so I'll pick this up again tomorrow."
        if exc.period == "month":
            return "I've used this month's budget, so I'm pausing until it frees up."
        return f"I've reached my US${self.settings.max_spend_usd:.0f} spending cap, so I'm paused."

    def _owner_turn(
        self, s, sender: Sender, ctx: Context, aud: Audience, msg: InboundMessage, inbound, emit: Emit | None = None
    ) -> tuple[str, list[str], dict]:
        """The owner's own conversation: a tool loop that can look things up, set reminders,
        search the web and ask for approval, with every answer citing its sources."""
        from superaide.brain.loop import OwnerTurn
        from superaide.factory import brain_ready
        from superaide.intentions.service import owner_zone
        from superaide.intentions.when import zone

        assert sender.person is not None
        owner_tz = zone(owner_zone(s, sender.person))
        timeline = owner_timeline(s, sender.person.id, ctx, thread_id=thread_of(msg), tz=owner_tz)
        brief = owner_brief(s, sender, aud)
        playbook_arm: dict | None = None
        if self.settings.skill_ratchet_enabled:
            from superaide.trust import ratchet

            if text := ratchet.live_text(s, sender.person):  # only owner-edited, approved playbooks
                use = ratchet.use_for_task()  # the coin flip: with the playbooks, or without (the control)
                playbook_arm = {"used": use}
                if use:
                    brief = brief + "\n\n" + text
        turn = OwnerTurn(
            llm=self.llm,
            model=self.settings.model_chat,
            agent_name=self.name,
            session=s,
            sender=sender,
            audience=aud,
            brief=brief,
            inbound=inbound,
            web=brain_ready(self.settings) and self.settings.web_tools_enabled,
            emit=emit,
            learns=brain_ready(self.settings),
        )
        try:
            result = turn.run(build_messages(timeline.events, msg.text, timeline.labels, tz=owner_tz))
        except BudgetExceeded as exc:
            return self._budget_reply(s, exc), ["budget"], {}
        except AgentFrozen:
            return "I'm paused right now.", ["frozen"], {}
        except Exception as exc:
            if (told := self._key_rejected(s, exc, owner=True)) is None:
                raise
            return told, ["key_rejected"], {}
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
        meta: dict = {"sources": sources} if sources else {}
        if result.activity:
            meta["activity"] = result.activity[:20]
        if result.reasoning:
            meta["reasoning"] = result.reasoning
        if playbook_arm is not None:
            meta["playbook_arm"] = playbook_arm
        if timeline.labels:
            meta["other_channels"] = len(timeline.labels)  # turns from other channels this reply saw
        reply = strip_label(result.text)
        if self.settings.second_opinion_enabled and brain_ready(self.settings):
            from superaide.trust.second_opinion import review

            reply = review(self, s, sender.person, msg.text, reply)
        return reply, [*result.notes, f"steps:{result.steps}"], meta

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
            return self._budget_reply(s, exc, owner=is_owner), ["budget"]
        except AgentFrozen:
            return ("I'm paused right now.", ["frozen"])
        except Exception as exc:
            if (told := self._key_rejected(s, exc, owner=is_owner)) is None:
                raise
            return told, ["key_rejected"]

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

    def _reply(
        self,
        s,
        ctx: Context,
        aud: Audience,
        text: str,
        thread: str = "",
        meta: dict | None = None,
        *,
        to: str | None = None,
    ) -> None:
        """`to` is set only for a model-written reply to someone who isn't the owner: on Telegram and Slack it
        then ends with the Principal Card line (PRINCIPAL_CARD_ENABLED), as Gmail replies do (#135)."""
        messenger = self.messengers.get(ctx.provider)
        if messenger is None:
            audit(s, "pipeline", "no_messenger", provider=ctx.provider)
            return
        text = self._steady(s, text)
        # The stored event keeps the Markdown and the sources list as they are (the app shows
        # both). Over a channel the "[n]" marks need the list beside them: the owner's own
        # replies carry it as a footer (only _owner_turn sets sources); anywhere else the
        # marks are dropped so they don't point at nothing.
        sources = (meta or {}).get("sources") or None
        wire = text
        if to is not None and ctx.provider in {"telegram", "slack"}:
            from superaide.principal import card

            wire = text + card.footer(s, channel=ctx.provider, to=to)
        try:
            if sources and ctx.kind == "dm":
                messenger.send_to_context(ctx, wire, thread, sources=sources)
            else:
                messenger.send_to_context(ctx, strip_citations(wire), thread)
        except DeliveryFailed as exc:  # health is recorded by the messenger; keep the turn and its record
            log.warning("reply not delivered on %s: %s", ctx.provider, exc.kind)
            audit(s, "pipeline", "reply_not_delivered", provider=ctx.provider, reason=exc.kind)
            meta = {**(meta or {}), "undelivered": exc.kind}
        except Exception as exc:  # e.g. a Slack rate limit: the turn's work and its record are kept
            log.error("reply not sent on %s: %s", ctx.provider, type(exc).__name__)
            audit(s, "pipeline", "reply_not_delivered", provider=ctx.provider, reason=type(exc).__name__)
            meta = {**(meta or {}), "undelivered": type(exc).__name__}
        log_event(s, context=ctx, direction="out", content=text, audience=aud, thread_id=thread, meta=meta)
