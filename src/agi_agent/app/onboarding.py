"""The agent onboards the person who hired it, like a new colleague would.

The conversation is driven by the server so it is stored like any other chat:
every line Ava says and every answer is an event in the in-app conversation,
and what Ava learns becomes private memories visible only to that person.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agi_agent.app.accounts import linked_providers, web_message
from agi_agent.audit import audit
from agi_agent.brain.llm import SystemBlock
from agi_agent.context.audience import Audience
from agi_agent.db.models import Event, MemoryItem, Onboarding, Person
from agi_agent.identity.resolver import resolve
from agi_agent.memory.store import get_or_create_context, log_event

STEPS = ["hello", "about", "goals", "voice", "rules", "channels", "first_task", "done"]
LABELS = {
    "hello": "Hello",
    "about": "About you",
    "goals": "Goals",
    "voice": "Your voice",
    "rules": "Ground rules",
    "channels": "Stay in touch",
    "first_task": "First task",
}
GOALS = [
    "Grow my audience",
    "Win clients",
    "Hire great people",
    "Build my personal brand",
    "Share what I learn",
    "Promote a product",
]
TONES = ["Direct", "Warm", "Playful", "Technical", "Story-led", "Contrarian"]
RULES = [
    "I'm an AI, and I'll always say so to anyone I talk to.",
    "What you tell me in private stays private. I never repeat it in a group or to someone else.",
    "Nothing gets posted, sent or deleted without your approval.",
    "Everything I remember is in my notebook. You can read it and remove anything.",
    "If someone claims to be you from a new account, I check with you first.",
]
TOPIC_IDEAS = ["What I learned this month", "A lesson from a recent project", "Why I'm building with AI"]


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


def lines_for(step: str, person: Person, agent_name: str, answers: dict[str, Any]) -> list[str]:
    name = first_name(person)
    if step == "hello":
        return [
            f"Hi {name}, I'm {agent_name}. Thanks for hiring me.",
            "Before anything else: I'm an AI, not a person. I don't have feelings, but I keep a careful notebook, "
            "and I get better at working with you every week.",
            "Right now I'm at the Infant stage. I watch, ask questions and draft. Nothing I write goes anywhere "
            "unless you say yes.",
        ]
    if step == "about":
        return ["Tell me a little about what you do. A sentence or two is plenty."]
    if step == "goals":
        return ["Thanks. What do you want LinkedIn to do for you? Pick as many as fit."]
    if step == "voice":
        return [
            "Now your voice. Paste a post you wrote and liked, or one by someone whose style you'd like to borrow. "
            "I'll study it. Then pick a few words for how you like to sound.",
        ]
    if step == "rules":
        return ["Before we start, here's how I work, so there are no surprises."]
    if step == "channels":
        return ["Where should I find you? We can always talk right here. I can also be on your phone through Telegram."]
    if step == "first_task":
        return ["Last thing. Want me to draft your first post? Give me a topic, or pick one of these."]
    if step == "done":
        return [f"That's us set up, {name}. Everything you told me is in my notebook. Let's get to work."]
    return []


def input_for(step: str, *, telegram_available: bool, telegram_linked: bool, answers: dict[str, Any]) -> dict[str, Any]:
    if step == "hello":
        return {"kind": "chips", "options": ["Nice to meet you", "What comes after Infant?"]}
    if step == "about":
        return {
            "kind": "text",
            "placeholder": "e.g. I lead applied AI at a consultancy in Singapore",
            "multiline": True,
        }
    if step == "goals":
        return {"kind": "multi", "options": GOALS, "other": True, "submit": "These are my goals"}
    if step == "voice":
        return {
            "kind": "voice",
            "placeholder": "Paste a post here (optional)",
            "options": TONES,
            "submit": "That's my voice",
        }
    if step == "rules":
        return {"kind": "rules", "items": RULES, "submit": "Sounds good"}
    if step == "channels":
        return {
            "kind": "channels",
            "telegram_available": telegram_available,
            "telegram_linked": telegram_linked,
            "invite_url": answers.get("telegram_invite_url"),
        }
    if step == "first_task":
        return {"kind": "topic", "options": TOPIC_IDEAS, "placeholder": "Or type your own topic", "skip": "Not now"}
    return {"kind": "none"}


def state_for(session: Session, person: Person) -> Onboarding:
    row = session.scalar(select(Onboarding).where(Onboarding.person_id == person.id))
    if row is None:
        row = Onboarding(person_id=person.id, step="hello", answers={})
        session.add(row)
        session.flush()
    return row


class Onboarder:
    def __init__(self, agent: Any, telegram_username: str | None) -> None:
        self.agent = agent  # pipeline.Agent: its model, messengers and settings
        self.settings = agent.settings
        self.telegram_username = telegram_username

    # --- views -------------------------------------------------------------------

    def view(
        self, session: Session, person: Person, extra: list[str] | None = None, hint: str | None = None
    ) -> StepView:
        state = state_for(session, person)
        step = state.step
        index = STEPS.index(step)
        providers = linked_providers(session, person)
        return StepView(
            step=step,
            index=index,
            total=len(LABELS),
            labels=list(LABELS.values()),
            ava=(extra or []) + lines_for(step, person, self.settings.agent_name, state.answers or {}),
            input=input_for(
                step,
                telegram_available=bool(self.telegram_username),
                telegram_linked="telegram" in providers,
                answers=state.answers or {},
            ),
            done=step == "done",
            hint=hint,
        )

    def start(self, session: Session, person: Person) -> None:
        """Called once at hire: Ava's first lines go into the conversation."""
        self._say(session, person, lines_for("hello", person, self.settings.agent_name, {}))

    def ensure_started(self, session: Session, person: Person) -> None:
        """A manager who never went through hiring in the app (added from the command
        line, say) still gets Ava's introduction the first time they open it."""
        if state_for(session, person).step != "hello":
            return
        _, ctx, _ = self._context(session, person)
        said_anything = session.scalar(select(Event.id).where(Event.context_id == ctx.id).limit(1))
        if said_anything is None:
            self.start(session, person)

    # --- answers -------------------------------------------------------------------

    def answer(
        self, session: Session, person: Person, step: str, payload: dict[str, Any]
    ) -> tuple[StepView, str | None]:
        """Record an answer and move on. Returns the next view, plus a topic when
        the person asked for a first draft: the caller runs that chat turn after
        this transaction commits (it uses its own transaction)."""
        state = state_for(session, person)
        if step != state.step:
            return self.view(session, person), None  # stale tab: show where we really are
        answers = dict(state.answers or {})
        extra: list[str] = []
        name = first_name(person)

        if step == "hello":
            choice = str(payload.get("choice", "Nice to meet you"))
            self._hear(session, person, choice)
            if choice.startswith("What comes after"):
                extra = [
                    "As I learn, I move up: Child (I draft and suggest on my own), Adolescent (I can publish inside "
                    "themes you've approved, with time for you to stop me) and Adult (I work on my own within my "
                    "limits). I only move up when the numbers show I've learned, and you sign off.",
                ]
        elif step == "about":
            text = str(payload.get("text", "")).strip()
            if not text:
                return self.view(session, person, hint="Even one line helps. What do you do?"), None
            self._hear(session, person, text)
            answers["about"] = text[:1000]
            self._remember(session, person, f"About {person.display_name}: {text[:1000]}")
        elif step == "goals":
            picked = [str(g) for g in payload.get("goals", []) if str(g).strip()][:8]
            other = str(payload.get("other", "")).strip()
            if other:
                picked.append(other[:200])
            if not picked:
                return self.view(session, person, hint="Pick at least one, or write your own."), None
            self._hear(session, person, ", ".join(picked))
            answers["goals"] = picked
            self._remember(session, person, f"{person.display_name}'s LinkedIn goals: {', '.join(picked)}")
        elif step == "voice":
            sample = str(payload.get("sample", "")).strip()[:3000]
            tones = [str(t) for t in payload.get("tones", []) if str(t).strip()][:6]
            said = ", ".join(tones) if tones else "No tone picked"
            self._hear(session, person, (f"{said}. Here's a post I like:\n\n{sample}" if sample else said))
            answers["tones"] = tones
            if tones:
                self._remember(session, person, f"{person.display_name} likes to sound: {', '.join(tones)}")
            if sample:
                answers["sample"] = sample
                self._remember(session, person, f"A post {person.display_name} likes, as a style reference: {sample}")
                summary = self._study_voice(sample, tones)
                if summary:
                    self._remember(
                        session, person, f"What {self.settings.agent_name} noticed about the voice: {summary}"
                    )
                    extra = [f"Here's what I noticed: {summary}"]
                else:
                    extra = ["Saved. I'll study it properly once my Claude brain is connected."]
        elif step == "rules":
            self._hear(session, person, "Sounds good")
            answers["rules_accepted_at"] = datetime.now(UTC).isoformat()
            audit(session, "app", "ground_rules_accepted", person_id=person.id)
        elif step == "channels":
            choice = str(payload.get("choice", "here"))
            if choice == "telegram" and self.telegram_username:
                from agi_agent.app.accounts import create_invite

                code = create_invite(session, person, "telegram")
                answers["telegram_invite_url"] = f"https://t.me/{self.telegram_username}?start={code}"
                state.answers = answers
                return (
                    self.view(
                        session,
                        person,
                        hint="Open Telegram with the button and press Start. The link works once, for 15 minutes.",
                    ),
                    None,
                )
            label = {"here": "Here in the app is fine", "telegram_done": "I've connected Telegram"}.get(choice, choice)
            self._hear(session, person, label)
            answers.pop("telegram_invite_url", None)
        elif step == "first_task":
            topic = str(payload.get("topic", "")).strip()
            if topic:
                answers["first_topic"] = topic[:300]
                state.answers = answers
                state.step = "done"
                state.completed_at = datetime.now(UTC)
                audit(session, "app", "onboarding_completed", person_id=person.id)
                session.flush()
                return self.view(session, person), topic[:300]
            self._hear(session, person, "Not now")

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
        self._say(session, person, lines_for(nxt, person, self.settings.agent_name, answers))
        _ = name
        return self.view(session, person, extra), None

    def draft_first_post(self, person_id: Any, topic: str) -> None:
        """After the onboarding transaction commits: a normal chat turn writes the
        draft into the conversation, then Ava's closing line follows it."""
        from agi_agent.db.session import session_scope

        with session_scope() as s:
            person = s.get(Person, person_id)
        if person is None:
            return
        self.agent.handle(web_message(person, f"Please draft my first LinkedIn post about: {topic}"))
        with session_scope() as s:
            person = s.get(Person, person_id)
            self._say(s, person, lines_for("done", person, self.settings.agent_name, {}))

    # --- helpers -------------------------------------------------------------------

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

    def _hear(self, session: Session, person: Person, text: str) -> None:
        sender, ctx, aud = self._context(session, person)
        log_event(
            session, context=ctx, direction="in", content=text, audience=aud, sender=sender, meta={"onboarding": True}
        )

    def _remember(self, session: Session, person: Person, content: str) -> None:
        _, ctx, _aud = self._context(session, person)
        session.add(
            MemoryItem(
                content=content,
                subject_person_ids=[person.id],
                origin_context_id=ctx.id,
                allowed_kind="persons",
                allowed_person_ids=[person.id],
                min_trust=2,
            )
        )
        audit(session, "memory", "remembered", source="onboarding")

    def _study_voice(self, sample: str, tones: list[str]) -> str | None:
        from agi_agent.factory import uses_real_model

        if not uses_real_model(self.settings):
            return None
        try:
            result = self.agent.llm.complete(
                purpose="onboarding_voice",
                model=self.settings.model_chat,
                system=[
                    SystemBlock(
                        "You study a LinkedIn post to learn the writer's voice. Reply with two short sentences, "
                        "addressed to the writer as 'you', naming concrete habits (openings, sentence length, "
                        "structure, tone). No preamble, no flattery."
                    )
                ],
                messages=[
                    {"role": "user", "content": f"Tones they picked: {', '.join(tones) or 'none'}\n\nPost:\n{sample}"}
                ],
                max_tokens=160,
            )
            return result.text.strip() or None
        except Exception:  # budget cap, kill switch or API error: onboarding carries on
            return None
