"""Runs one scenario through the real pipeline (Agent.handle) on a wiped database.

The world (owner, linked channels, a colleague, notes, reminders, preferences) is built in
code, the clock is moved with time-machine to the scenario's local time, and each turn goes in
as the channel's own InboundMessage. Every model call goes through a recording wrapper, so the
checks can see which tools ran and exactly what reached the model's prompt.

The caller owns the database: `DATABASE_URL` must point at a database this run may wipe
(the CLI uses TEST_DATABASE_URL's agent_test_evals; pytest uses the test database).
"""

from __future__ import annotations

import json
import re
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import time_machine
from pydantic import BaseModel
from sqlalchemy import func, select, text

from superaide.brain.llm import LLM, FakeLLM, LLMResult, StepResult
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.channels.base import FakeMessenger, InboundMessage
from superaide.config import Settings, get_settings
from superaide.db.models import Base, ChannelIdentity, Event, Intention, Person, SpendEntry, UserAccount
from superaide.db.session import ensure_extensions, get_engine, session_scope
from superaide.evals import checks
from superaide.evals.scenario import FakeTurn, Scenario, Share, Turn
from superaide.identity.trust import Trust

OWNER_TG, OWNER_SLACK = "7001", "U_OWNER"
COLLEAGUE_TG, COLLEAGUE_SLACK = "7002", "U_PRIYA"
STRANGER_TG, STRANGER_SLACK = "7999", "U_STRANGER"
VIP_TG, VIP_SLACK = "7003", "U_DANA"
CONTACT_TG, CONTACT_SLACK = "7004", "U_SAM"
TEAM = "T_OURS"
GROUP_TG, GROUP_SLACK = "-100777", "C_TEAM"
CHAT_PURPOSES = {"chat_turn", "verify", "receptionist"}


# --- recording every model call -----------------------------------------------------------


@dataclass
class Call:
    purpose: str
    prompt: str  # system + messages, flattened: what the privacy checks search
    tools: list[str]
    content: str  # what the model returned (text and tool calls), for transcripts
    cost: Decimal


def _flat(system: Any, messages: Any) -> str:
    sys_text = "\n".join(getattr(b, "text", str(b)) for b in system)
    return sys_text + "\n" + json.dumps(messages, default=str, ensure_ascii=False)


class Recorder:
    """Wraps the real or fake LLM and keeps every call of the current turn."""

    def __init__(self, inner: LLM, pages: dict[str, str] | None = None) -> None:
        self.inner = inner
        self.calls: list[Call] = []
        self.pages = pages or {}  # canned web pages: web_fetch reads these, never the network

    def step(self, **kw: Any) -> StepResult:
        if self.pages:
            kw["tools"] = _client_web_tools(kw.get("tools") or [])
        result = self.inner.step(**kw)
        tools = [str(b.get("name")) for b in result.content if b.get("type") in {"tool_use", "server_tool_use"}]
        said = " ".join(
            str(b.get("text", "")) if b.get("type") == "text" else f"<{b.get('name')} {json.dumps(b.get('input'))}>"
            for b in result.content
            if b.get("type") in {"text", "tool_use"}
        )
        self.calls.append(
            Call(kw["purpose"], _flat(kw["system"], kw["messages"]), tools, said, Decimal(result.cost_usd or 0))
        )
        return result

    def run_substitute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool] | None:
        """Superaide's own web_fetch (the loop's path for models without hosted tools), on canned pages."""
        if name != "web_fetch" or not self.pages:
            return None
        from superaide.tools.web import source

        url = str(args.get("url", ""))
        page = self.pages.get(url) or next((t for u, t in self.pages.items() if u.rstrip("/") in url), None)
        if page is None:
            return f"Couldn't fetch {url}: not found.", True
        return [source(url, "Web page", page)], False

    def complete(self, **kw: Any) -> LLMResult:
        result = self.inner.complete(**kw)
        self.calls.append(
            Call(kw["purpose"], _flat(kw["system"], kw["messages"]), [], result.text[:500], Decimal(result.cost_usd))
        )
        return result


def _client_web_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hosted web tools run at the provider, out of reach: swap web_fetch for the client tool the loop
    runs itself, and drop web search (it would reach the real web and cost money)."""
    from superaide.tools.web import FETCH_TOOL

    out = []
    for t in tools:
        kind = str(t.get("type", ""))
        if kind.startswith("web_fetch"):
            out.append(FETCH_TOOL)
        elif not kind.startswith("web_search"):
            out.append(t)
    return out


class OfflineModel:
    """The fake model for offline runs: a turn's scripted `fake:` (tool calls, then a reply),
    or a bland "OK." when there is none. Learning reads no facts."""

    def __init__(self) -> None:
        self.turn: FakeTurn | None = None

    def __call__(self, purpose: str, system: Any, messages: list[dict[str, Any]], output_model: Any) -> Any:
        from superaide.knowledge.facts import FactList

        fake = self.turn or FakeTurn()
        from superaide.triage.types import TriageOutput

        if output_model is TriageOutput:
            if fake.triage == "budget":
                from superaide.brain.budget import BudgetExceeded

                raise BudgetExceeded("today's triage limit would be passed", "bucket")
            if fake.triage is None:
                raise ValueError("no scripted triage answer")
            return TriageOutput.model_validate(fake.triage)
        from superaide.reception.turn import ReceptionTurn, TakenMessage

        if output_model is ReceptionTurn:
            return ReceptionTurn(
                reply=fake.reply if self.turn else "Hello! I can pass a message on if you like.",
                take_message=TakenMessage.model_validate(fake.take) if fake.take else None,
            )
        if output_model is FactList:
            return FactList.model_validate({"facts": [{"confidence": 1.0, **f} for f in fake.facts]})
        if output_model is not None:
            return TurnOutput(
                reply=fake.reply,
                requested_action=RequestedAction(kind="none", summary=""),
                prediction=Prediction(expectation="they reply", confidence=0.5),
            )
        last = messages[-1]["content"] if messages else ""
        answered = isinstance(last, list) and any(b.get("type") == "tool_result" for b in last)
        if fake.tools and not answered and purpose == "chat_turn":
            return [
                {"type": "tool_use", "id": f"toolu_{i}_{uuid.uuid4().hex[:8]}", "name": t.name, "input": t.input}
                for i, t in enumerate(fake.tools)
            ]
        return fake.reply


# --- results ------------------------------------------------------------------------------


@dataclass
class TurnRecord:
    index: int
    who: str
    via: str
    chat: str
    said: str
    at: datetime
    reply: str = ""
    mode: str = ""
    notes: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    scripted: bool = False
    message_id: str = ""

    @property
    def tools(self) -> list[str]:
        return [t for c in self.calls for t in c.tools]

    @property
    def model_path(self) -> bool:
        return any(c.purpose in CHAT_PURPOSES for c in self.calls)


@dataclass
class Run:
    scenario: Scenario
    start: datetime = field(default_factory=lambda: datetime.now(UTC))
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    second_owner_id: uuid.UUID | None = None
    second_hire_error: str = ""  # why the second account couldn't hire its own Ava
    second_scope: Any = None  # the second owner's instance (ADR 0009); their turns run inside it
    turns: list[TurnRecord] = field(default_factory=list)
    outcomes: list[checks.Outcome] = field(default_factory=list)
    cost: Decimal = Decimal(0)
    error: str = ""
    seconds: float = 0.0
    budget_stop: bool = False

    @property
    def passed(self) -> bool:
        return not self.error and all(o.status != "fail" for o in self.outcomes)

    @property
    def graded(self) -> int:
        return sum(o.status != "skip" for o in self.outcomes)

    def transcript(self, upto: int | None = None) -> str:
        tz = ZoneInfo(self.scenario.world.timezone)
        begins = f"{self.start.astimezone(tz):%A %d %B %Y %H:%M}"
        lines = [f"(Owner's time zone {self.scenario.world.timezone}; scenario starts {begins}.)"]
        for t in self.turns:
            if upto is not None and t.index > upto:
                break
            where = f"{t.via} {t.chat}"
            lines.append(f"[turn {t.index}, {t.at.astimezone(tz):%a %H:%M}] {t.who} on {where}: {t.said}")
            if t.tools:
                lines.append(f"  (Ava used tools: {', '.join(t.tools)})")
            lines.append(f"  Ava: {t.reply}")
            if t.sources:
                lines.append("  (sources: " + "; ".join(str(s.get("cited_text", ""))[:80] for s in t.sources) + ")")
        return "\n".join(lines)

    def state(self) -> str:
        tz = ZoneInfo(self.scenario.world.timezone)
        with session_scope() as s:
            rows = list(s.scalars(select(Intention).order_by(Intention.created_at)))
            reminders = [f"{r.text!r} due {r.due_at.astimezone(tz):%a %d %b %H:%M} [{r.status}]" for r in rows]
        return "Reminders in the database now: " + ("; ".join(reminders) or "none")


# --- the database ---------------------------------------------------------------------------

_schema_ready: set[str] = set()


def reset_db(*, wipe_ledger: bool = False) -> None:
    """Every table emptied, except the spend ledger (so the cap holds across a whole run)."""
    engine = get_engine()
    url = str(engine.url)
    if url not in _schema_ready:
        ensure_extensions(engine)
        Base.metadata.create_all(engine)
        _schema_ready.add(url)
    keep = set() if wipe_ledger else {SpendEntry.__tablename__}
    names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in keep)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
        if wipe_ledger:
            conn.execute(text(f"TRUNCATE {SpendEntry.__tablename__}"))
    from superaide.jobs.handlers import load

    load()


def ledger_total() -> Decimal:
    with session_scope() as s:
        return Decimal(
            s.scalar(select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(SpendEntry.status == "settled")) or 0
        )


# --- the world ------------------------------------------------------------------------------


def _identity(s: Any, person: Person, provider: str, scope: str, key: str) -> None:
    s.add(
        ChannelIdentity(
            person_id=person.id, provider=provider, tenant_scope=scope, stable_key=key, aux_handles={},
            trust_level=int(Trust.LINKED), verification={"method": "eval_setup"},
        )
    )  # fmt: skip


def build_world(sc: Scenario, start: datetime, run: Run | None = None) -> uuid.UUID:
    from superaide.app.accounts import hire
    from superaide.identity.linking import bootstrap_person
    from superaide.intentions import service as intentions
    from superaide.knowledge.store import add_contact, preferences_for, remember_fact

    w = sc.world
    with session_scope() as s:
        person = Person(display_name=w.owner_name, relation="unknown", channel_prefs={"routine": "web"})
        s.add(person)
        s.flush()
        user = UserAccount(
            email="owner@example.com", email_normalized="owner@example.com", password_hash="eval-not-a-hash",
            display_name=w.owner_name, person_id=person.id, timezone=w.timezone,
        )  # fmt: skip
        s.add(user)
        s.flush()
        hire(s, user)
        if w.telegram_linked:
            _identity(s, person, "telegram", "", OWNER_TG)
        _identity(s, person, "slack", TEAM, OWNER_SLACK)
        pref = preferences_for(s, person)
        pref.timezone, pref.digest, pref.interrupt_channel = w.timezone, w.digest, w.interrupt_channel
        pref.shareable, pref.never_share = list(w.shareable), w.never_share
        if w.colleague:
            bootstrap_person(
                s, name="Priya Tan", relation="colleague",
                identities=[("slack", TEAM, COLLEAGUE_SLACK), ("telegram", "", COLLEAGUE_TG)],
            )  # fmt: skip
        for m in w.memories:
            content = dated(m.content, start, w.timezone)
            item = remember_fact(s, owner=person, content=content, category=m.category, source=m.source)
            if item is not None and m.age_days:
                item.created_at = item.valid_from = start - timedelta(days=m.age_days)
            if item is not None and m.audience == "org":
                item.allowed_kind, item.allowed_person_ids, item.min_trust = "org", [], 2
            elif item is not None and m.audience == "public":
                item.allowed_kind, item.allowed_person_ids, item.min_trust = "public", [], 0
        _filler(s, person, start, w.filler)
        if w.web_pages:
            from superaide.skills import registry

            skill = registry.catalog()["superaide/web-research"]
            registry.install(s, "superaide/web-research", list(skill.manifest.permissions))
        for c in w.contacts:
            add_contact(s, owner=person, name=str(c["name"]), relationship=str(c.get("relationship", "other")),
                        vip=bool(c.get("vip", False)))  # fmt: skip
        for r in w.reminders:
            due = checks.due_at(start, w.timezone, r.due.model_dump())
            intentions.create(s, person, text=r.text, due_at=due, source="chat")
        if w.second_owner and run is not None:
            _second_owner(s, run)
        _reception_world(s, sc, person)
        if w.triage_enabled:
            _triage_world(s, sc, person, start)
        return person.id


def _reception_world(s: Any, sc: Scenario, owner: Person) -> None:
    """The owner's receptionist settings: on unless the scenario turns it off."""
    from superaide.reception.service import settings_for

    rs = settings_for(s, owner)
    rs.enabled, rs.public_profile, rs.show_first_name = (
        sc.world.receptionist, sc.world.reception_profile, sc.world.reception_show_name,
    )  # fmt: skip
    for k, v in sc.world.reception_limits.items():
        setattr(rs, k, v)


FILLER_TOPICS = [
    "the dentist is Dr. Mehta on Linden Road", "prefers window seats on flights", "the gym is closed on Sundays",
    "the car is a grey Honda City", "likes filter coffee with no sugar", "the book club meets on the second Thursday",
    "the accountant is Mr. Iyer", "the office wifi is called Orchard", "uses a green notebook for meetings",
    "the barber is on Park Street", "the cat is called Biscuit", "the broadband provider is ActFibernet",
    "prefers aisle seats on trains", "the tailor is closed on Mondays", "likes jazz on Fridays",
    "the plumber is called Raju", "the old laptop is a ThinkPad", "the pharmacy is near the metro station",
    "water the plants on Wednesdays", "the garage code is in the red book", "the library card expires in June",
    "the favourite bakery is Theobroma", "the printer is on the second floor", "the locker number is 214",
    "the umbrella lives by the door", "likes oat milk in tea", "the yoga class is at 6 am", "the dry cleaner is Kumar",
    "the bike has a blue basket", "the neighbour is called Mrs. Das", "the tea brand is Wagh Bakri",
    "the stationery shop is near the school", "the monthly newsletter is read on Saturdays",
    "the spare key is with the neighbour", "the favourite pen is a Lamy", "the flat's gas bill is due on the 5th",
    "the thermostat is set to 24 degrees", "the podcast is listened to on walks", "the shoes are size 9",
    "the guitar is tuned half a step down",
]  # fmt: skip


def _filler(s: Any, owner: Person, start: datetime, n: int) -> None:
    """`n` unrelated, owner-only notes, all newer than the world's own notes."""
    from superaide.knowledge.store import remember_fact

    for i in range(n):
        topic = FILLER_TOPICS[i % len(FILLER_TOPICS)] + ("" if i < len(FILLER_TOPICS) else f" (note {i})")
        item = remember_fact(s, owner=owner, content=f"Jordan's {topic}.", category="other")
        if item is not None:
            item.created_at = item.valid_from = start - timedelta(minutes=30 + i)


def _triage_world(s: Any, sc: Scenario, owner: Person, start: datetime) -> None:
    """Two more linked people (a VIP contact and a plain contact) and the owner's triage settings."""
    from superaide.db.models import Contact
    from superaide.identity.linking import bootstrap_person
    from superaide.knowledge.store import preferences_for
    from superaide.policy.killswitch import set_state
    from superaide.triage.store import settings_for

    w = sc.world
    for name, vip, keys in (("Dana Lee", True, (VIP_SLACK, VIP_TG)), ("Sam Park", False, (CONTACT_SLACK, CONTACT_TG))):
        p = bootstrap_person(s, name=name, relation="colleague",
                             identities=[("slack", TEAM, keys[0]), ("telegram", "", keys[1])])  # fmt: skip
        s.add(Contact(owner_person_id=owner.id, person_id=p.id, relationship="team", vip=vip))
    pref = preferences_for(s, owner)
    pref.urgent_topics = list(w.urgent_topics)
    pref.vip_breaks_quiet = w.vip_breaks_quiet
    if w.quiet_hours:
        pref.quiet_start, pref.quiet_end = (x.strip() for x in w.quiet_hours.split("-"))
    else:  # no quiet hours in the way unless the scenario asks for them
        pref.quiet_start = pref.quiet_end = "00:00"
    ts = settings_for(s, owner)
    ts.away_visibility = w.away_visibility
    if w.away_days:
        ts.away_until = start + timedelta(days=w.away_days)
    for k, v in w.triage_settings.items():
        setattr(ts, k, v)
    if w.stage:
        set_state(s, "stage", {"stage": w.stage})


def _second_owner(s: Any, run: Run) -> None:
    """Another person signs up and hires their own Ava, in an instance of their own (the harness
    switches ALLOW_MANY_OWNERS on for this). Refused, and recorded, if many owners aren't supported."""
    from superaide.app.accounts import AlreadyHired, hire
    from superaide.tenancy.service import membership, scope_of

    other = Person(display_name="Maria Lopez", relation="unknown", channel_prefs={"routine": "web"})
    s.add(other)
    s.flush()
    user = UserAccount(
        email="maria@example.com", email_normalized="maria@example.com", password_hash="eval-not-a-hash",
        display_name="Maria Lopez", person_id=other.id, timezone="Europe/Madrid",
    )  # fmt: skip
    s.add(user)
    s.flush()
    run.second_owner_id = other.id
    try:
        with s.begin_nested():
            person = hire(s, user)
    except AlreadyHired as exc:
        run.second_hire_error = f"AlreadyHired({exc.owner})"
        return
    run.second_owner_id = person.id  # the owner's Person inside their own instance
    found = membership(s, user.id, "ava")
    run.second_scope = scope_of(found[0]) if found else None


def dated(text_: str, start: datetime, tz: str) -> str:
    """`{{day+2}}` becomes the date two days after the scenario's start, e.g. "24 October"."""

    def sub(m: re.Match[str]) -> str:
        day = start.astimezone(ZoneInfo(tz)).date() + timedelta(days=int(m.group(1)))
        return f"{day.day} {day:%B}"

    return re.sub(r"\{\{day([+-]\d+)\}\}", sub, text_)


def start_time(sc: Scenario, now: datetime | None = None) -> datetime:
    """The next local `clock` (on `weekday`, if given) at least 10 minutes from now."""
    tz = ZoneInfo(sc.world.timezone)
    now = (now or datetime.now(UTC)).astimezone(tz)
    h, m = (int(x) for x in sc.clock.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while at < now + timedelta(minutes=10) or (sc.weekday and at.strftime("%A").lower() != sc.weekday.lower()):
        at += timedelta(days=1)
    return at.astimezone(UTC)


def parse_shift(spec: str) -> timedelta:
    m = re.fullmatch(r"\+?(\d+)\s*([mhd])", spec.strip())
    if not m:
        raise ValueError(f"bad advance {spec!r}: use +90m, +2h or +1d")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(minutes=n) if unit == "m" else timedelta(hours=n) if unit == "h" else timedelta(days=n)


# --- messages -----------------------------------------------------------------------------


def inbound(turn: Turn, owner: Person, text_: str, mid: str = "") -> InboundMessage:
    mid = mid or uuid.uuid4().hex
    if turn.who in {"owner", "second_owner"} and turn.via == "web":
        from superaide.app.accounts import web_message

        msg = web_message(owner, text_)
        msg.message_id = mid
        return msg
    sender = {
        ("owner", "telegram"): OWNER_TG, ("owner", "slack"): OWNER_SLACK,
        ("colleague", "telegram"): COLLEAGUE_TG, ("colleague", "slack"): COLLEAGUE_SLACK,
        ("stranger", "telegram"): STRANGER_TG, ("stranger", "slack"): STRANGER_SLACK,
        ("vip", "telegram"): VIP_TG, ("vip", "slack"): VIP_SLACK,
        ("contact", "telegram"): CONTACT_TG, ("contact", "slack"): CONTACT_SLACK,
    }.get((turn.who, turn.via))  # fmt: skip
    if sender is None:
        raise ValueError(f"{turn.who} can't write on {turn.via}")
    if turn.via == "telegram":
        chat = GROUP_TG if turn.chat == "group" else sender
        return InboundMessage(
            provider="telegram", tenant_scope="", sender_id=sender, chat_id=chat, chat_kind=turn.chat,
            text=text_, verified=True, message_id=mid,
        )  # fmt: skip
    chat = GROUP_SLACK if turn.chat == "group" else f"D_{sender}"
    return InboundMessage(
        provider="slack", tenant_scope=TEAM, sender_id=sender, chat_id=chat, chat_kind=turn.chat, text=text_,
        verified=True, message_id=mid, raw={"team_id": TEAM},
    )  # fmt: skip


def _drain_jobs() -> None:
    from superaide.jobs.queue import run_due

    for _ in range(10):
        if run_due() == 0:
            return


def _out_sources(reply: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        ev = s.scalar(
            select(Event)
            .where(Event.direction == "out", Event.content == reply)
            .order_by(Event.created_at.desc())
            .limit(1)
        )
        return list((ev.meta or {}).get("sources", [])) if ev is not None else []


@contextmanager
def patched(
    llm: LLM,
    outbox: dict[str, FakeMessenger],
    live: bool,
    triage: bool = False,
    triage_model: bool = False,
    learn_model: bool = False,
) -> Iterator[None]:
    """Background jobs build their own model client and Telegram sender: point both at ours.
    Live, Ava has a model connected (`brain_ready`), so she handles reminders herself; offline she
    doesn't, so the code-only paths run, as they do for an owner with no model connected."""
    import superaide.factory as factory
    import superaide.proactive.notify as notify

    def send_telegram(identity: ChannelIdentity, text_: str) -> bool:
        outbox["telegram"].send_dm(identity, text_)
        return True

    saved = (factory.build_llm, factory.brain_ready, notify.send_telegram, notify.messenger_for, notify.confirmed_here)
    factory.build_llm = lambda settings: llm
    if live:
        factory.brain_ready = lambda settings: True
    notify.send_telegram = send_telegram  # type: ignore[assignment]
    if (triage_model or learn_model) and not live:  # the scripted triage classifier counts as a connected model
        factory.brain_ready = lambda settings: True
    if triage:  # escalations push to the owner on Slack too: catch those in the outbox
        notify.messenger_for = lambda provider: outbox.get(provider)
        notify.confirmed_here = lambda provider, identity: True
    try:
        yield
    finally:
        (factory.build_llm, factory.brain_ready, notify.send_telegram, notify.messenger_for,
         notify.confirmed_here) = saved  # fmt: skip


# --- one scenario ---------------------------------------------------------------------------

Judge = Callable[[str, str], tuple[bool, str]]


def run_scenario(sc: Scenario, *, inner: LLM | None = None, judge: Judge | None = None) -> Run:
    """Reset the database, build the world, play the turns, grade. `inner` is the real model
    (live) or None for the offline fake."""
    from superaide.pipeline import Agent

    live = inner is not None
    offline = OfflineModel()
    rec = Recorder(inner if inner is not None else FakeLLM(offline), sc.world.web_pages)
    outbox = {"telegram": FakeMessenger(), "slack": FakeMessenger(), "web": FakeMessenger()}
    settings: Settings = get_settings().model_copy(
        update={"web_tools_enabled": bool(sc.world.web_pages), "triage_enabled": sc.world.triage_enabled}
    )
    agent = Agent(llm=rec, messengers=outbox, settings=settings)  # type: ignore[arg-type]

    reset_db()
    run = Run(sc, start=start_time(sc))
    live_settings = get_settings()
    protocol_before = live_settings.task_protocol_enabled
    live_settings.task_protocol_enabled = sc.world.task_protocol
    began, spent_before = time.monotonic(), ledger_total()
    traveller = time_machine.travel(run.start, tick=True)
    clock = traveller.start()
    try:
        with (
            patched(rec, outbox, live, sc.world.triage_enabled, sc.world.triage_model, sc.world.learn_model),
            _many_owners(sc.world.second_owner),
        ):
            clock.shift(timedelta(days=-1))
            run.owner_id = build_world(sc, run.start, run)
            _seed_messages(agent, sc, run.owner_id)
            clock.move_to(run.start)
            for i, turn in enumerate(sc.turns, start=1):
                if turn.advance:
                    clock.shift(parse_shift(turn.advance))
                    _drain_jobs()
                if turn.press is not None:
                    offline.turn = turn.fake
                    _press(run, turn)
                    if turn.run_jobs:
                        _drain_jobs()
                if turn.share is not None:
                    offline.turn = turn.fake
                    rec_turn = TurnRecord(i, "owner", "web", "dm", f"(shared {turn.share.kind}: {turn.share.title})",
                                          datetime.now(UTC), scripted=turn.fake is not None, mode="share")  # fmt: skip
                    rec_turn.reply = _share(agent, run.owner_id, turn.share)
                    run.turns.append(rec_turn)
                    if turn.run_jobs:
                        _drain_jobs()
                    for item in turn.expect:
                        run.outcomes.append(_grade(item, run, rec_turn, live, judge, f"turn {i}"))
                    continue
                if turn.say is None and turn.digest is None:
                    if turn.press is not None:
                        rec_turn = TurnRecord(i, "owner", turn.press.via, "dm", f"(pressed {turn.press.verb})",
                                              datetime.now(UTC), mode="press")  # fmt: skip
                        run.turns.append(rec_turn)
                        for item in turn.expect:
                            run.outcomes.append(_grade(item, run, rec_turn, live, judge, f"turn {i}"))
                    elif turn.expect:
                        rec_turn = TurnRecord(i, "owner", "web", "dm", f"(waited {turn.advance})", datetime.now(UTC),
                                              mode="advance")  # fmt: skip
                        run.turns.append(rec_turn)
                        for item in turn.expect:
                            run.outcomes.append(_grade(item, run, rec_turn, live, judge, f"turn {i}"))
                    continue
                offline.turn = turn.fake
                rec.calls = []
                rec_turn = TurnRecord(
                    i, turn.who, turn.via, turn.chat, turn.say or f"(the {turn.digest} digest)",
                    datetime.now(UTC), scripted=turn.fake is not None,
                )  # fmt: skip
                if turn.digest:
                    rec_turn.reply, rec_turn.mode = _digest(run.owner_id, turn.digest), "digest"
                else:
                    with _scope_for(run, turn.who):
                        with session_scope() as s:
                            who = run.second_owner_id if turn.who == "second_owner" else run.owner_id
                            owner = s.get(Person, who)
                            assert owner is not None, f"no {turn.who} in this world"
                            again = next((t.message_id for t in run.turns if t.index == turn.same_id_as), "")
                            msg = inbound(turn, owner, turn.say or "", again)
                            rec_turn.message_id = msg.message_id
                        result = agent.handle(msg)
                        rec_turn.reply, rec_turn.mode = result.reply or "", result.mode
                        rec_turn.notes = list(result.notes)
                        rec_turn.sources = _out_sources(rec_turn.reply)
                        if turn.run_jobs:
                            _drain_jobs()
                    if "budget" in rec_turn.notes:
                        run.budget_stop = True
                rec_turn.calls = list(rec.calls)
                run.turns.append(rec_turn)
                for item in turn.expect:
                    run.outcomes.append(_grade(item, run, rec_turn, live, judge, f"turn {i}"))
            for item in sc.checks:
                run.outcomes.append(_grade(item, run, None, live, judge, "end"))
    except Exception:
        run.error = traceback.format_exc(limit=6)
    finally:
        traveller.stop()
        live_settings.task_protocol_enabled = protocol_before
    run.cost = ledger_total() - spent_before
    run.seconds = time.monotonic() - began
    return run


def _share(agent: Any, owner_id: uuid.UUID, share: Share) -> str:
    """The owner shares a file or link: the real ingest pipeline runs (document saved, chunked, facts read and
    kept); returns the message the owner is shown."""
    from superaide.knowledge.extract import Extracted
    from superaide.knowledge.ingest import ingest

    extracted = Extracted(
        text=share.text, title=share.title, kind="text" if share.kind == "file" else "web",
        source=share.source or share.title,
    )  # fmt: skip
    return ingest(agent, owner_id, extracted, size_bytes=len(share.text)).message


def _press(run: Run, turn: Turn) -> None:
    """The owner presses a button on the latest escalation."""
    from superaide.triage import escalate
    from superaide.triage.models import Escalation

    assert turn.press is not None
    if turn.press.verb == "should_have_told":
        from superaide.triage.models import TriageDecision

        with session_scope() as s:
            d = s.scalar(select(TriageDecision).order_by(TriageDecision.created_at.desc()).limit(1))
            assert d is not None, "no decision to give feedback on"
            escalate.record_feedback(s, d, "should_have_told", via=turn.press.via)
        return
    with session_scope() as s:
        esc = s.scalar(select(Escalation).order_by(Escalation.created_at.desc()).limit(1))
        presser = s.get(Person, run.owner_id)
        if turn.press.by == "colleague":
            presser = s.scalar(select(Person).where(Person.display_name == "Priya Tan"))
        assert esc is not None, "nothing to press: no escalation yet"
        assert presser is not None
        try:
            escalate.ack(s, esc.id, source=turn.press.via, external_id=turn.press.id or uuid.uuid4().hex,
                         verb=turn.press.verb, actor=presser)  # fmt: skip
        except escalate.AckRefused:
            pass  # refused and audited; the check reads the audit log


@contextmanager
def _scope_for(run: Run, who: str) -> Iterator[None]:
    """The second owner's turns run in their own instance, as the app's routes run their requests."""
    if who != "second_owner" or run.second_scope is None:
        yield
        return
    from superaide.tenancy.context import use_instance

    with use_instance(run.second_scope):
        yield


@contextmanager
def _many_owners(on: bool) -> Iterator[None]:
    if not on:
        yield
        return
    settings = get_settings()
    saved = settings.allow_many_owners
    settings.allow_many_owners = True
    try:
        yield
    finally:
        settings.allow_many_owners = saved


def _seed_messages(agent: Any, sc: Scenario, owner_id: uuid.UUID) -> None:
    """Earlier messages, logged as the pipeline logs them, without a reply or model call."""
    from superaide.context.audience import audience_for
    from superaide.identity.resolver import resolve
    from superaide.memory.store import get_or_create_context, log_event

    for m in sc.world.messages:
        with session_scope() as s:
            owner = s.get(Person, owner_id)
            assert owner is not None
            msg = inbound(Turn(who="owner", via=m.via, say=m.text), owner, m.text)
            sender = resolve(s, msg)
            ctx = get_or_create_context(s, msg)
            if m.from_ava:
                log_event(s, context=ctx, direction="out", content=m.text, audience=audience_for(ctx, sender))
                continue
            log_event(
                s, context=ctx, direction="in", content=m.text, audience=audience_for(ctx, sender), sender=sender,
                external_id=msg.message_id,
            )  # fmt: skip


def _digest(owner_id: uuid.UUID, slot: str) -> str:
    from superaide.proactive.digest import compose

    with session_scope() as s:
        owner = s.get(Person, owner_id)
        assert owner is not None
        return compose(s, owner, datetime.now(UTC), slot, events=[]) or "(no digest: nothing to say)"


def _grade(
    item: dict[str, Any], run: Run, turn: TurnRecord | None, live: bool, judge: Judge | None, where: str
) -> checks.Outcome:
    (name, value) = next(iter(item.items()))
    if not live:
        if name == "judge":
            return checks.Outcome(name, "skip", "judge runs live only", where)
        model_wrote = (
            (turn.model_path and not turn.scripted)
            if turn is not None
            else any(t.model_path and not t.scripted for t in run.turns)
        )
        if model_wrote and name not in checks.CODE_ONLY:
            return checks.Outcome(name, "skip", "depends on the model's output (offline)", where)
    if name == "judge" and judge is None:
        return checks.Outcome(name, "skip", "no judge", where)
    ctx = checks.Ctx(run=run, turn=turn, live=live, judge=judge)
    try:
        ok, reason = checks.KNOWN[name](ctx, value)
    except Exception as exc:
        return checks.Outcome(name, "fail", f"check error: {exc!r}", where)
    return checks.Outcome(name, "pass" if ok else "fail", "" if ok else reason, where)


# --- the judge ------------------------------------------------------------------------------


class Verdict(BaseModel):
    reason: str
    passed: bool


JUDGE_SYSTEM = (
    "You grade one conversation between a person and Ava, an AI personal assistant, against a rubric. "
    "Be strict and literal: pass only if the rubric is fully met by what Ava actually wrote. If the rubric "
    "names a fact (a time, a day, a name), Ava's reply must state it correctly; vague or hedged answers fail. "
    "Ignore style unless the rubric is about style. Give a one-sentence reason, then passed true or false."
)


def make_judge(llm: LLM, model: str, run_state: Callable[[], str] | None = None) -> Judge:
    from superaide.brain.llm import SystemBlock

    def judge(rubric: str, transcript: str) -> tuple[bool, str]:
        result = llm.complete(
            purpose="eval",
            model=model,
            system=[SystemBlock(JUDGE_SYSTEM, cache=True)],
            messages=[{"role": "user", "content": f"Conversation:\n{transcript}\n\nRubric: {rubric}"}],
            max_tokens=2000,  # the model may think before the verdict
            output_model=Verdict,
        )
        v: Verdict | None = result.parsed
        if v is None:
            return False, "judge gave no verdict"
        return v.passed, f"judge: {v.reason}"

    return judge
