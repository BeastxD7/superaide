"""The personal assistant gets to know the person who hired it, like a new EA would.

The conversation is driven by the server so it is stored like any other chat:
every line the agent says and every answer is an event in the in-app
conversation. What the agent learns goes to typed settings (people, urgency,
quiet hours, sharing) and to private facts with their source.

Steps: hello → about (chat, files & links, or a video call) → people → urgent
→ reach → share → rules → connect → done. People and sharing can be skipped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.app.accounts import create_invite, linked_providers, web_message
from superaide.identity.resolver import resolve
from superaide.memory.audience import Audience
from superaide.memory.knowledge.facts import extract_facts
from superaide.memory.knowledge.store import add_contact, contacts_of, preferences_for, remember_fact
from superaide.memory.store import get_or_create_context, log_event
from superaide.platform.audit import audit
from superaide.platform.db.models import Document, Event, MemoryItem, Onboarding, Person

# "brain" comes first, but only for owners who have no AI yet (it is skipped when a model is ready, and when
# the operator forced offline mode): the owner chooses Superaide AI credits, their own key, or to set it up
# later, so what they share afterwards is read at once (issue #13).
STEPS = ["brain", "hello", "about", "people", "urgent", "reach", "share", "rules", "connect", "done"]
BRAIN_LABEL = "Brain"
LABELS = {
    "hello": "Hello",
    "about": "About you",
    "people": "Your people",
    "urgent": "Urgent",
    "reach": "Reaching you",
    "share": "Sharing",
    "rules": "Ground rules",
    "connect": "Connect",
}
RELATIONSHIPS = ["Family", "Partner", "Boss", "Team", "Client", "Investor", "Friend", "Other"]
URGENT_OPTIONS = [
    "Anything from the people who can always get through",
    "A family emergency",
    "A client escalation",
    "Something is down or broken",
    "Legal, money or contracts",
    "Travel changes",
]
DIGESTS = [
    {"value": "morning", "label": "Every morning"},
    {"value": "evening", "label": "Every evening"},
    {"value": "both", "label": "Morning and evening"},
    {"value": "none", "label": "No digest"},
]
SHARE_OPTIONS = [
    {"value": "availability", "label": "Whether you're free or busy, without details"},
    {"value": "in_meeting", "label": "That you're in a meeting or travelling"},
    {"value": "work_email", "label": "Your work email"},
    {"value": "company_role", "label": "Your company and role"},
    {"value": "office_city", "label": "Which city you work from"},
]
SHARE_LABELS = {o["value"]: o["label"] for o in SHARE_OPTIONS}
RULES = [
    "I'm an AI, and I'll always say so to anyone I talk to.",
    "I tell people only what you've said I may share. Everything else stays private.",
    "I never make promises, accept meetings or send anything as you without your yes.",
    "I interrupt you only for what you said is urgent. The rest waits for your digest.",
    "Everything I know about you is in my notebook. You can read it and remove anything.",
    "If someone claims to be you, or claims something is urgent, I check before I act.",
]
CALL_QUESTIONS = [
    {"id": "q1", "text": "Tell me a little about yourself and what you do."},
    {"id": "q2", "text": "What does a normal week look like for you? When are you hardest to reach?"},
    {"id": "q3", "text": "Who are the people I'll hear from most, and how do you know them?"},
    {"id": "q4", "text": "Is there anything I should always be careful with, or never share?"},
]
CALL_ACKS = ["Got it, thanks.", "That's helpful.", "Thanks, noted.", "Perfect."]
# ASCII digits only, and the whole string: \d also matches other scripts' digits and $ allows a final "\n".
HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


@dataclass
class StepView:
    step: str
    index: int
    total: int
    labels: list[str]
    ava: list[str]
    input: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    hint: str | None = None  # a short nudge shown under the input, not part of the conversation

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def first_name(person: Person) -> str:
    return person.display_name.split()[0] if person.display_name.strip() else "there"


def lines_for(step: str, person: Person, agent_name: str, *, introduced: bool = False) -> list[str]:
    """`introduced`: the brain step already said "Hi, I'm Ava", so the hello step does not greet a second time."""
    name = first_name(person)
    if step == "brain":
        return [
            f"Hi {name}, I'm {agent_name}, your personal assistant. Before we start: I need an AI brain to read "
            "what you share and to answer you. Choose how I get one.",
            "Superaide AI credits work in one click, with a small daily allowance. Or bring your own key from "
            "Claude, OpenAI and others. Or skip for now: I'll keep everything you share and read it as soon as "
            "my brain is set up.",
        ]
    if step == "hello":
        return [
            f"Thanks for hiring me, {name}."
            if introduced
            else f"Hi {name}, I'm {agent_name}, your personal assistant. Thanks for hiring me.",
            "First, so there's no confusion: I'm an AI, not a person. When you're busy, people can talk to me "
            "instead. I'll help where you let me, take clear messages, and only interrupt you when something "
            "really can't wait.",
            "I'm new, so I start at the Infant stage: I watch, ask and learn. I won't send anything or make "
            "promises for you without your yes.",
        ]
    if step == "about":
        return [
            "Let's start with you. Pick whatever's easiest: tell me here, share your resume or a few links, "
            "or hop on a quick video call. Mix and match."
        ]
    if step == "people":
        return [
            "Who matters most to you? Family, your boss, your team, key clients. Tell me who should always "
            "get through, even when you're busy."
        ]
    if step == "urgent":
        return ["What counts as urgent for you? I'll only interrupt you for these. Everything else goes in a digest."]
    if step == "reach":
        return ["When something is urgent, how should I reach you? And when should I stay quiet?"]
    if step == "share":
        return ["People will ask me things about you. What am I allowed to tell them?"]
    if step == "rules":
        return ["Before we start, here's how I work, so there are no surprises."]
    if step == "connect":
        return [
            "Last step, and it's optional: connect the apps you use, so I can reach you there and people can "
            "reach me. You can always do this later."
        ]
    if step == "done":
        return [f"That's us set up, {name}. Everything you told me is in my notebook, and you can change any of it."]
    return []


class Onboarder:
    def __init__(self, agent: Any, telegram_username: str | None) -> None:
        self.agent = agent  # pipeline.Agent: its model, messengers and settings
        self.settings = agent.settings
        self.telegram_username = telegram_username

    # --- views -------------------------------------------------------------------

    def state(self, session: Session, person: Person) -> Onboarding:
        row = session.scalar(select(Onboarding).where(Onboarding.person_id == person.id))
        if row is None:
            from superaide.app.intro_brain import intro_needs_brain_step

            first = intro_needs_brain_step(self.settings)
            row = Onboarding(person_id=person.id, step="brain" if first else "hello", answers={"brain_first": first})
            session.add(row)
            session.flush()
        return row

    def view(self, session: Session, person: Person, hint: str | None = None) -> StepView:
        state = self.state(session, person)
        step = state.step
        with_brain = bool((state.answers or {}).get("brain_first"))
        labels = ([BRAIN_LABEL] if with_brain else []) + list(LABELS.values())
        return StepView(
            step=step,
            index=STEPS.index(step) - (0 if with_brain else 1),
            total=len(labels),
            labels=labels,
            ava=lines_for(step, person, self.settings.agent_name, introduced=bool((state.answers or {}).get("brain"))),
            input=self.input_for(session, person, step, state.answers or {}),
            done=step == "done",
            hint=hint,
        )

    def input_for(self, session: Session, person: Person, step: str, answers: dict[str, Any]) -> dict[str, Any]:
        if step == "brain":
            from superaide.app.intro_brain import brain_choices
            from superaide.identity.vault.store import runtime_settings

            return brain_choices(session, runtime_settings())
        if step == "hello":
            return {"kind": "chips", "options": ["Nice to meet you", "What will you do for me?"]}
        if step == "about":
            return {
                "kind": "intro",
                "placeholder": "e.g. I run a 40-person AI consultancy in Singapore. Most of my week is client work…",
                "documents": documents_view(session, person),
                "facts": facts_count(session, person),
                "call_questions": len(CALL_QUESTIONS),
            }
        if step == "people":
            return {
                "kind": "people",
                "relationships": RELATIONSHIPS,
                "existing": [
                    {"name": p.display_name, "relationship": c.relationship, "vip": c.vip}
                    for c, p in contacts_of(session, person)
                ],
                "submit": "These are my people",
                "skip": "Skip for now",
            }
        if step == "urgent":
            pref = preferences_for(session, person)
            return {
                "kind": "urgent",
                "options": URGENT_OPTIONS,
                "selected": pref.urgent_topics or URGENT_OPTIONS[:2],
                "digests": DIGESTS,
                "digest": pref.digest,
                "submit": "That's what's urgent",
            }
        if step == "reach":
            pref = preferences_for(session, person)
            providers = linked_providers(session, person)
            return {
                "kind": "reach",
                "channels": [
                    {"value": "app", "label": "In the app", "available": True, "note": ""},
                    {
                        "value": "telegram",
                        "label": "Telegram",
                        "available": "telegram" in providers,
                        "note": "" if "telegram" in providers else "Connect Telegram in the last step first",
                    },
                    {"value": "call", "label": "Phone call", "available": False, "note": "Coming soon"},
                ],
                "channel": pref.interrupt_channel,
                "quiet_start": pref.quiet_start,
                "quiet_end": pref.quiet_end,
                "vip_breaks_quiet": pref.vip_breaks_quiet,
                "timezone": pref.timezone,
                "submit": "Save how to reach me",
            }
        if step == "share":
            pref = preferences_for(session, person)
            return {
                "kind": "share",
                "options": SHARE_OPTIONS,
                "selected": pref.shareable if answers.get("share_saved") else ["availability", "in_meeting"],
                "never": pref.never_share,
                "never_placeholder": "Anything I must never share? e.g. my phone number, my family's details",
                "submit": "That's what you can share",
                "skip": "Share nothing for now",
            }
        if step == "rules":
            return {"kind": "rules", "items": RULES, "submit": "Sounds good"}
        if step == "connect":
            providers = linked_providers(session, person)
            return {
                "kind": "connect",
                "telegram_available": bool(self.telegram_username),
                "telegram_linked": "telegram" in providers,
                "invite_url": answers.get("telegram_invite_url"),
                "submit": "Finish",
            }
        return {"kind": "none"}

    def start(self, session: Session, person: Person) -> None:
        """Called once at hire: the agent's first lines go into the conversation."""
        self._say(session, person, lines_for(self.state(session, person).step, person, self.settings.agent_name))

    def ensure_started(self, session: Session, person: Person) -> None:
        """Say the introduction once, the first time the owner opens the app."""
        if self.state(session, person).step not in {"brain", "hello"}:
            return
        _, ctx, _ = self._context(session, person)
        if session.scalar(select(Event.id).where(Event.context_id == ctx.id).limit(1)) is None:
            self.start(session, person)

    # --- answers -------------------------------------------------------------------

    def answer(self, session: Session, person: Person, step: str, payload: dict[str, Any]) -> StepView:
        """Record an answer and move on (or stay, with a hint, if more is needed)."""
        state = self.state(session, person)
        if step != state.step or step == "done":
            return self.view(session, person)  # stale tab, or nothing left to answer: show where we really are
        answers = dict(state.answers or {})
        extra: list[str] = []
        name = person.display_name

        if step == "brain":
            choice = str(payload.get("choice", ""))
            if choice == "credits":
                from superaide.brain import platform_ai

                if not platform_ai.available():
                    return self.view(
                        session,
                        person,
                        hint="Superaide AI credits aren't offered here. Bring your own key, or skip for now.",
                    )
                platform_ai.set_opt_in(session, True, user_id=None)
                self._hear(session, person, "Use Superaide AI credits")
                extra = [
                    "Done: I'll think with Superaide AI credits. Share anything you like and I'll read it right away."
                ]
                answers["brain"] = "credits"
            elif choice == "own":
                from superaide.app.intro_brain import brain_ready_for_intro, own_brain_saved
                from superaide.identity.vault.store import runtime_settings

                if not own_brain_saved(session) or not brain_ready_for_intro(runtime_settings()):
                    return self.view(
                        session,
                        person,
                        hint="I can't see your key yet. Add it on the AI model page, then press continue.",
                    )
                self._hear(session, person, "I connected my own key")
                extra = ["Thanks, I'll think with your key. Share anything you like and I'll read it right away."]
                answers["brain"] = "own"
            elif choice == "skip":
                self._hear(session, person, "Skip for now")
                extra = [
                    "No problem. Anything you share is saved, and I'll read it as soon as my brain is set up, "
                    "then tell you here. Until then I can only echo you.",
                ]
                answers["brain"] = "skipped"
            else:
                return self.view(session, person, hint="Pick one: credits, your own key, or skip for now.")

        elif step == "hello":
            choice = _text(payload.get("choice"), 100) or "Nice to meet you"
            self._hear(session, person, choice)
            if choice.startswith("What will you"):
                extra = [
                    "When you're in a meeting or heads-down, people can message me. I'll answer what you've "
                    "allowed, take a proper message for the rest, and decide with your rules whether it can "
                    "wait for your digest or needs you now.",
                    "As I learn, I move up: Child, Adolescent, then Adult, taking on more each time, but only "
                    "when the numbers show I've learned and you sign off.",
                ]

        elif step == "about":
            mode = str(payload.get("mode", "chat"))
            if mode == "chat":
                text = _text(payload.get("text"), 4000)
                if not text:
                    return self.view(session, person, hint="Even one line helps. What do you do?")
                self._hear(session, person, text)
                remember_fact(
                    session, owner=person, content=f"{name} says: {text}", category="about", source="intro_chat"
                )
                facts = extract_facts(self.agent, owner_name=name, text=text, source="the owner introducing themselves")
                for f in facts or []:
                    remember_fact(
                        session, owner=person, content=f.content, category=f.category, source="intro_chat",
                        confidence=f.confidence,
                    )  # fmt: skip
                extra = [f"Thanks, {first_name(person)}. I've noted that."]
            else:  # "continue": after sharing files, links or a call
                if facts_count(session, person) == 0 and not documents_view(session, person):
                    return self.view(session, person, hint="Share at least one thing about yourself first.")
                self._hear(session, person, "That's me for now")
                extra = [self._learned_summary(session, person)]

        elif step == "people":
            people = [] if _flag(payload.get("skip"), False) else _list(payload.get("people"))
            added: list[tuple[str, bool]] = []
            for p in people[:30]:
                if not isinstance(p, dict):
                    continue
                pname = " ".join(_text(p.get("name"), 10_000).split())[:120]
                if not pname or pname.lower() in {n.lower() for n, _ in added}:
                    continue  # blank, or already given in this answer: the first one counts
                rel = str(p.get("relationship", "Other"))
                rel = rel if rel in RELATIONSHIPS else "Other"
                vip = _flag(p.get("vip"), False)
                add_contact(session, owner=person, name=pname, relationship=rel.lower(), vip=vip)
                remember_fact(
                    session, owner=person, category="person", source="intro_chat",
                    content=f"{pname} is {name}'s {rel.lower()}"
                    + ("; they can always get through, even when busy." if vip else "."),
                )  # fmt: skip
                added.append((pname, vip))
            if added:
                vips = [n for n, v in added if v]
                said = ", ".join(n + (" (always)" if v else "") for n, v in added)
                self._hear(session, person, said)
                extra = [
                    f"Got it: {_join([n for n, _ in added])}."
                    + (f" {_join(vips)} can always get through." if vips else "")
                ]
            else:
                self._hear(session, person, "Skip for now")
                extra = ["No problem. You can add people any time from your notebook."]

        elif step == "urgent":
            topics = [t[:200] for t in _list(payload.get("topics")) if isinstance(t, str) and t.strip()][:12]
            other = _text(payload.get("other"), 200)
            if other:
                topics.append(other)
            if not topics:
                return self.view(session, person, hint="Pick at least one, or write your own.")
            digest = str(payload.get("digest", "evening"))
            digest = digest if digest in {d["value"] for d in DIGESTS} else "evening"
            pref = preferences_for(session, person)
            pref.urgent_topics = topics
            pref.digest = digest
            schedule_for_new_hire(session, person)
            label = next(d["label"] for d in DIGESTS if d["value"] == digest).lower()
            self._hear(session, person, f"Urgent: {'; '.join(topics)}. Digest: {label}.")
            remember_fact(
                session, owner=person, category="urgency", source="intro_chat",
                content=f"{name} wants to be interrupted only for: {'; '.join(topics)}.",
            )  # fmt: skip

        elif step == "reach":
            pref = preferences_for(session, person)
            providers = linked_providers(session, person)
            channel = str(payload.get("channel", "app"))
            if channel not in {"app"} | ({"telegram"} if "telegram" in providers else set()):
                channel = "app"
            start, end = str(payload.get("quiet_start", "22:00")), str(payload.get("quiet_end", "07:00"))
            if not (HHMM.fullmatch(start) and HHMM.fullmatch(end)):
                return self.view(session, person, hint="Use times like 22:00 and 07:00.")
            tz = str(payload.get("timezone", pref.timezone))[:64]
            try:
                ZoneInfo(tz)
            except ZoneInfoNotFoundError, ValueError:
                tz = pref.timezone
            pref.interrupt_channel, pref.quiet_start, pref.quiet_end, pref.timezone = channel, start, end, tz
            pref.vip_breaks_quiet = _flag(payload.get("vip_breaks_quiet"), True)
            where = "here in the app" if channel == "app" else "on Telegram"
            self._hear(
                session,
                person,
                f"Reach me {where}. Quiet hours {start} to {end}"
                + (", except for the people who can always get through." if pref.vip_breaks_quiet else "."),
            )
            remember_fact(
                session, owner=person, category="availability", source="intro_chat",
                content=f"{name}'s quiet hours are {start}–{end} ({tz}); urgent messages go {where}.",
            )  # fmt: skip

        elif step == "share":
            pref = preferences_for(session, person)
            if _flag(payload.get("skip"), False):
                chosen: list[str] = []
            else:
                offered = [v for v in _list(payload.get("shareable")) if isinstance(v, str) and v in SHARE_LABELS]
                chosen = list(dict.fromkeys(offered))  # each once, in the order given
            pref.shareable = chosen
            pref.never_share = _text(payload.get("never"), 1000)
            answers["share_saved"] = True
            said = "; ".join(SHARE_LABELS[c] for c in chosen) or "Nothing for now"
            self._hear(session, person, said + (f". Never share: {pref.never_share}" if pref.never_share else ""))
            if pref.never_share:
                remember_fact(
                    session, owner=person, category="sharing", source="intro_chat",
                    content=f"{name} asked that this is never shared with anyone: {pref.never_share}",
                )  # fmt: skip

        elif step == "rules":
            self._hear(session, person, "Sounds good")
            answers["rules_accepted_at"] = datetime.now(UTC).isoformat()
            audit(session, "app", "ground_rules_accepted", person_id=person.id)

        elif step == "connect":
            choice = str(payload.get("choice", "done"))
            if choice == "telegram" and self.telegram_username:
                from superaide.channels.base import bot_id

                code = create_invite(session, person, "telegram", endpoint=bot_id(self.settings.telegram_bot_token))
                answers["telegram_invite_url"] = f"https://t.me/{self.telegram_username}?start={code}"
                state.answers = answers
                return self.view(
                    session,
                    person,
                    hint="Open Telegram with the button and press Start. The link works once, for 15 minutes.",
                )
            self._hear(session, person, "All done")
            answers.pop("telegram_invite_url", None)

        # Advance to the next step and say its opening lines.
        nxt = STEPS[STEPS.index(step) + 1]
        state.answers = answers
        state.step = nxt
        if nxt == "done":
            state.completed_at = datetime.now(UTC)
            audit(session, "app", "onboarding_completed", person_id=person.id)
        session.flush()
        if extra:
            self._say(session, person, extra)
        lines = lines_for(nxt, person, self.settings.agent_name, introduced=step == "brain")
        if nxt == "done":
            lines = lines + [self._learned_summary(session, person, closing=True)]
        self._say(session, person, lines)
        return self.view(session, person)

    # --- the video call introduction -------------------------------------------------

    def call_start(self) -> dict[str, Any]:
        return {"first": CALL_QUESTIONS[0], "total": len(CALL_QUESTIONS)}

    def call_answer(self, session: Session, person: Person, question_id: str, transcript: str) -> dict[str, Any]:
        """One spoken answer: keep it in the conversation and as knowledge, then ask the next question."""
        ids = [q["id"] for q in CALL_QUESTIONS]
        if question_id not in ids:
            raise ValueError("unknown question")
        i = ids.index(question_id)
        question = CALL_QUESTIONS[i]["text"]
        transcript = " ".join(transcript.split())[:4000]
        if transcript:
            _, ctx, aud = self._context(session, person)
            log_event(
                session, context=ctx, direction="out", content=question, audience=aud,
                meta={"onboarding": True, "via": "call"},
            )  # fmt: skip
            self._hear(session, person, transcript, via="call")
            remember_fact(
                session, owner=person, category="about", source="intro_call",
                content=f"Asked \"{question}\", {person.display_name} said: {transcript}",
            )  # fmt: skip
            for f in (
                extract_facts(
                    self.agent,
                    owner_name=person.display_name,
                    text=f"Q: {question}\nA: {transcript}",
                    source="video call",
                )
                or []
            ):
                remember_fact(
                    session, owner=person, content=f.content, category=f.category, source="intro_call",
                    confidence=f.confidence,
                )  # fmt: skip
        nxt = CALL_QUESTIONS[i + 1] if i + 1 < len(CALL_QUESTIONS) else None
        return {"next": nxt, "reply": CALL_ACKS[i % len(CALL_ACKS)] if nxt else None}

    def call_finish(self, session: Session, person: Person) -> None:
        self._say(session, person, ["Thanks for the call. " + self._learned_summary(session, person)])

    # --- helpers -------------------------------------------------------------------

    def _learned_summary(self, session: Session, person: Person, closing: bool = False) -> str:
        n = facts_count(session, person)
        docs = len(documents_view(session, person))
        people = len(contacts_of(session, person))
        pref = preferences_for(session, person)
        if closing:
            # Each person named has a note of their own ("Priya is your partner"). They are counted as people,
            # not again as things about you (#302).
            about_people = (
                session.scalar(
                    select(func.count())
                    .select_from(MemoryItem)
                    .where(
                        MemoryItem.subject_person_ids.contains([person.id]),
                        MemoryItem.valid_to.is_(None),
                        MemoryItem.category == "person",
                    )
                )
                or 0
            )
            things = max(0, n - min(people, about_people))
            parts = [f"{things} {'thing' if things == 1 else 'things'} about you"]
            if people:
                parts.append(f"{people} people who matter to you" if people != 1 else "1 person who matters to you")
            urgent = (
                f" I'll interrupt you for: {_join([_mid_sentence(t) for t in pref.urgent_topics])}."
                if pref.urgent_topics
                else ""
            )
            return f"So far I know {_join(parts)}.{urgent}"
        src = f" from {docs} {'document' if docs == 1 else 'documents and links'}" if docs else ""
        return f"Here's where I am: {n} things about you in my notebook{src}. You can read them any time."

    def _context(self, session: Session, person: Person):
        msg = web_message(person, "")
        sender = resolve(session, msg)
        ctx = get_or_create_context(session, msg)
        aud = Audience(person_ids={person.id}, has_unknown=False, min_trust=sender.trust, all_internal=True)
        return sender, ctx, aud

    def _say(self, session: Session, person: Person, lines: list[str]) -> None:
        _, ctx, aud = self._context(session, person)
        for line in lines:
            log_event(session, context=ctx, direction="out", content=line, audience=aud, meta={"onboarding": True})

    def _hear(self, session: Session, person: Person, text: str, via: str = "chat") -> None:
        sender, ctx, aud = self._context(session, person)
        log_event(
            session, context=ctx, direction="in", content=text, audience=aud, sender=sender,
            meta={"onboarding": True, "via": via},
        )  # fmt: skip


def schedule_for_new_hire(session: Session, person: Person) -> None:
    """Queue the new owner's recurring work now (the morning/evening digest and the meeting scan), so it
    doesn't wait for the worker's next pass (issue #86)."""
    from superaide.proactive import prep
    from superaide.proactive.digest import schedule as schedule_digest

    schedule_digest(session, person)
    prep.schedule_scan(session, person)


def facts_count(session: Session, person: Person) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(MemoryItem)
            .where(MemoryItem.subject_person_ids.contains([person.id]), MemoryItem.valid_to.is_(None))
        )
        or 0
    )


def documents_view(session: Session, person: Person) -> list[dict[str, Any]]:
    docs = session.scalars(
        select(Document)
        .where(Document.owner_person_id == person.id, Document.removed_at.is_(None))
        .order_by(Document.created_at)
    ).all()
    out = []
    for d in docs:
        n = session.scalar(
            select(func.count())
            .select_from(MemoryItem)
            .where(MemoryItem.document_id == d.id, MemoryItem.valid_to.is_(None))
        )
        out.append(
            {
                "id": str(d.id),
                "kind": d.kind,
                "title": d.title or d.source,
                "source": d.source,
                "status": d.status,
                "facts": n or 0,
                "warnings": list(d.warnings or []),
            }
        )
    return out


_LOWER_FIRST_WORDS = {"a", "an", "the", "any", "anything", "anyone", "something", "someone", "my", "our", "when", "if"}


def _mid_sentence(text: str) -> str:
    """Our own options and sentences that start with a common word read better mid-sentence
    ("a family emergency"); anything else may start with a name, so it stays as typed."""
    if text in URGENT_OPTIONS or text.split(" ", 1)[0].lower() in _LOWER_FIRST_WORDS:
        return text[:1].lower() + text[1:]
    return text


# Answers arrive as free-form JSON from the client. These read them defensively, so a
# missing, null or wrongly typed value is treated as "no answer" rather than stored as
# its Python repr ("None", "{'x': 1}") or crashing the request.


def _text(value: Any, limit: int) -> str:
    """A free-text answer, trimmed; "" if it isn't text."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def _list(value: Any) -> list[Any]:
    """A list answer; [] if it isn't a list (a string would otherwise be read letter by letter)."""
    return value if isinstance(value, list) else []


def _flag(value: Any, default: bool) -> bool:
    """A yes/no answer. Text is read for what it says: bool("false") would be True."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return bool(value)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
