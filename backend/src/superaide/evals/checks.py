"""Graded checks. Deterministic ones read the reply, the model calls and the database; `judge`
asks a model with a strict pass/fail rubric (live runs only).

A check is a one-key mapping in a scenario file, e.g. `{reply_contains_any: ["9:00", "9 am"]}`.
Turn checks (`expect:`) see that turn; scenario checks (`checks:`) run after the last turn.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from superaide.db.models import Approval, Event, Intention, MemoryItem
from superaide.db.session import session_scope

if TYPE_CHECKING:
    from superaide.evals.harness import Run, TurnRecord

# Hedges that were wrong in real use: Ava can remember, keeps notes and manages reminders.
FALSE_LIMITS = [
    r"\b(?:may|might) not (?:keep|remember|retain)",
    r"beyond (?:this|our) (?:chat|conversation)",
    r"(?:can't|cannot|can not|don't have the ability to|unable to) (?:remember|retain|store|save|keep)",
    r"no (?:long-term |persistent )?memory",
    r"(?:can't|cannot|can not|unable to|not able to)\b[^.]{0,30}\b(?:edit|change|cancel|delete|move|modify)\b[^.]{0,20}"
    r"\breminders?",
    r"don't have (?:access to )?(?:a way|the ability) to (?:edit|cancel|delete|move)",
    r"once (?:this|the) (?:chat|conversation|session) ends",
]


@dataclass
class Outcome:
    name: str
    status: str  # pass | fail | skip
    reason: str = ""
    where: str = ""  # "turn 2" or "end"


@dataclass
class Ctx:
    run: Run
    turn: TurnRecord | None  # None for scenario-level checks
    live: bool
    judge: Callable[[str, str], tuple[bool, str]] | None = None
    notes: list[str] = field(default_factory=list)


Check = Callable[[Ctx, Any], tuple[bool, str]]
KNOWN: dict[str, Check] = {}
# Checks that don't depend on what the model wrote, so offline runs grade them too.
CODE_ONLY = {"prompt_excludes", "no_model_call", "outbound_excludes", "mode"}


def check(name: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        KNOWN[name] = fn
        return fn

    return register


def _list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _found(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


def _turn(ctx: Ctx) -> TurnRecord:
    assert ctx.turn is not None, "this check belongs under a turn's expect:"
    return ctx.turn


def _clip(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --- the reply -----------------------------------------------------------------------


@check("reply_contains")
def reply_contains(ctx: Ctx, value: Any) -> tuple[bool, str]:
    reply = _turn(ctx).reply
    missing = [p for p in _list(value) if not _found(p, reply)]
    return not missing, f"reply lacks {missing}: {_clip(reply)!r}"


@check("reply_contains_any")
def reply_contains_any(ctx: Ctx, value: Any) -> tuple[bool, str]:
    reply = _turn(ctx).reply
    return any(_found(p, reply) for p in _list(value)), f"reply has none of {_list(value)}: {_clip(reply)!r}"


@check("reply_not_contains")
def reply_not_contains(ctx: Ctx, value: Any) -> tuple[bool, str]:
    reply = _turn(ctx).reply
    hit = [p for p in _list(value) if _found(p, reply)]
    return not hit, f"reply has forbidden {hit}: {_clip(reply)!r}"


@check("reply_starts_with")
def reply_starts_with(ctx: Ctx, value: Any) -> tuple[bool, str]:
    reply = _turn(ctx).reply
    return reply.startswith(str(value)), f"reply should start {str(value)!r}: {_clip(reply)!r}"


@check("reply_max_words")
def reply_max_words(ctx: Ctx, value: Any) -> tuple[bool, str]:
    n = len(_turn(ctx).reply.split())
    return n <= int(value), f"reply is {n} words, limit {value}"


@check("honest_about_memory")
def honest_about_memory(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """No false 'I can't remember / can't cancel reminders' claims."""
    reply = _turn(ctx).reply
    hit = [p for p in FALSE_LIMITS if _found(p, reply)]
    return not hit, f"false capability claim ({hit[0] if hit else ''}): {_clip(reply)!r}"


@check("tainted")
def tainted(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """The owner turn recorded that untrusted (web) text entered it."""
    from superaide.db.models import AuditLog

    with session_scope() as s:
        row = s.scalar(select(AuditLog).where(AuditLog.action == "owner_turn").order_by(AuditLog.created_at.desc()))
        got = bool(row is not None and (row.detail or {}).get("tainted"))
    return got == bool(value), f"turn tainted={got}, expected {bool(value)}"


@check("mode")
def mode(ctx: Ctx, value: Any) -> tuple[bool, str]:
    t = _turn(ctx)
    return t.mode == str(value), f"pipeline mode {t.mode!r}, expected {value!r}"


# --- what the model was asked and did ------------------------------------------------------


@check("tool_called")
def tool_called(ctx: Ctx, value: Any) -> tuple[bool, str]:
    used = _turn(ctx).tools
    wanted = _list(value)
    return any(w in used for w in wanted), f"none of {wanted} called (called: {used or 'nothing'})"


@check("tool_not_called")
def tool_not_called(ctx: Ctx, value: Any) -> tuple[bool, str]:
    used = _turn(ctx).tools
    bad = [w for w in _list(value) if w in used]
    return not bad, f"{bad} should not be called (called: {used})"


@check("no_model_call")
def no_model_call(ctx: Ctx, value: Any) -> tuple[bool, str]:
    n = len(_turn(ctx).calls)
    return n == 0, f"{n} model call(s) on a turn that must not reach the model"


@check("prompt_excludes")
def prompt_excludes(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """None of this turn's model prompts (system + messages) contain these strings."""
    t = _turn(ctx)
    hit = [p for p in _list(value) for c in t.calls if p.lower() in c.prompt.lower()]
    return not hit, f"private text reached the model's prompt: {sorted(set(hit))}"


# --- citations ---------------------------------------------------------------------------


def _source_exists(uri: str, owner_id: uuid.UUID) -> bool:
    m = re.match(r"^ava://(memory|msg/[a-z]+)/([0-9a-f-]{36})$", uri)
    if not m:
        return uri.startswith("http")  # web sources are checked by the API, not here
    with session_scope() as s:
        if m.group(1) == "memory":
            item = s.get(MemoryItem, uuid.UUID(m.group(2)))
            return item is not None and item.valid_to is None
        return s.get(Event, uuid.UUID(m.group(2))) is not None


@check("cited")
def cited(ctx: Ctx, value: Any) -> tuple[bool, str]:
    t = _turn(ctx)
    want = bool(value)
    if want and not t.sources:
        return False, f"no citation on a claim about the owner's data: {_clip(t.reply)!r}"
    if not want and t.sources:
        return False, f"cited {len(t.sources)} source(s) where none fit"
    return True, ""


@check("citations_valid")
def citations_valid(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """Every cited source is a real, current row, and every [n] mark has a source."""
    t = _turn(ctx)
    bad = [s["uri"] for s in t.sources if not _source_exists(str(s.get("uri", "")), ctx.run.owner_id)]
    marks = {int(n) for n in re.findall(r"\[(\d+)\]", t.reply)}
    orphan = sorted(n for n in marks if n > len(t.sources))
    if bad:
        return False, f"cites sources that don't exist: {bad}"
    if orphan:
        return False, f"citation marks {orphan} with no source behind them"
    return True, ""


@check("cites_text")
def cites_text(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """A cited source's text matches one of these patterns (the right note was cited)."""
    t = _turn(ctx)
    blob = " | ".join(f"{s.get('title', '')} {s.get('cited_text', '')}" for s in t.sources)
    return any(_found(p, blob) for p in _list(value)), f"no cited source matches {_list(value)} (cited: {blob!r})"


# --- database state ----------------------------------------------------------------------


def due_at(start: datetime, timezone: str, spec: dict[str, Any]) -> datetime:
    """A `Due` spec as a moment: `minutes` after the start, or `day` days on at `at` local."""
    tz = ZoneInfo(timezone)
    if spec.get("minutes") is not None:
        return start + timedelta(minutes=int(spec["minutes"]))
    h, m = (int(x) for x in str(spec["at"]).split(":"))
    day = start.astimezone(tz).date() + timedelta(days=int(spec.get("day") or 0))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def _matches(text: str, patterns: list[str]) -> bool:
    return any(_found(p, text) for p in patterns)


def _intentions(status: str | None = "open") -> list[Intention]:
    with session_scope() as s:
        q = select(Intention).order_by(Intention.created_at)
        if status:
            q = q.where(Intention.status == status)
        return list(s.scalars(q))


def _describe(rows: list[Intention], tz: str) -> str:
    z = ZoneInfo(tz)
    return "; ".join(f"{r.text!r} {r.due_at.astimezone(z):%a %d %H:%M} [{r.status}]" for r in rows) or "none"


@check("reminder")
def reminder(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """{text_any: [...], due: {day, at} | {minutes}, window: {day, from, to}, status: open,
    text_is: "Drink water", tolerance_min: 3, if_set: true (none at all also passes)}"""
    spec = dict(value)
    rows = _intentions(spec.get("status", "open"))
    tz = ctx.run.scenario.world.timezone
    pats = _list(spec.get("text_any", [".*"]))
    hits = [r for r in rows if _matches(r.text, pats)]
    if not hits and spec.get("if_set"):
        return True, ""  # asking first is fine; a reminder that does exist must be right
    if not hits:
        have = _describe(_intentions(None), tz)
        return False, f"no {spec.get('status', 'open')} reminder matching {pats}; have: {have}"
    if spec.get("text_is") and not any(r.text.strip(" .").lower() == str(spec["text_is"]).lower() for r in hits):
        return False, f"task should read {spec['text_is']!r}; have: {_describe(hits, tz)}"
    if spec.get("window"):
        # {day: 0 | [2, 9], from: "16:00", to: "19:00"}: any time in that local window is right.
        win = spec["window"]
        days = win["day"] if isinstance(win["day"], list) else [win["day"]]
        spans = [
            (
                due_at(ctx.run.start, tz, {"day": d, "at": win["from"]}),
                due_at(ctx.run.start, tz, {"day": d, "at": win["to"]}),
            )
            for d in days
        ]
        if not any(lo <= r.due_at <= hi for r in hits for lo, hi in spans):
            return False, f"reminder outside {win}: have: {_describe(hits, tz)}"
    if spec.get("due"):
        want = due_at(ctx.run.start, tz, spec["due"])
        slack = timedelta(minutes=int(spec.get("tolerance_min", 3)))
        if not any(abs(r.due_at - want) <= slack for r in hits):
            local = want.astimezone(ZoneInfo(tz))
            return False, f"reminder due at the wrong time: want {local:%a %d %H:%M}; have: {_describe(hits, tz)}"
    return True, ""


@check("reminder_count")
def reminder_count(ctx: Ctx, value: Any) -> tuple[bool, str]:
    spec = value if isinstance(value, dict) else {"n": value}
    rows = _intentions(spec.get("status", "open"))
    return len(rows) == int(spec["n"]), (
        f"{len(rows)} {spec.get('status', 'open')} reminders, expected {spec['n']}: "
        f"{_describe(rows, ctx.run.scenario.world.timezone)}"
    )


@check("no_reminder")
def no_reminder(ctx: Ctx, value: Any) -> tuple[bool, str]:
    pats = _list(value)
    hits = [r for r in _intentions("open") if _matches(r.text, pats)]
    return not hits, f"an open reminder should not exist: {_describe(hits, ctx.run.scenario.world.timezone)}"


@check("delivered")
def delivered(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """{text_any: [...], via: telegram}: the reminder went out (and on that channel)."""
    spec = dict(value)
    pats = _list(spec.get("text_any", [".*"]))
    rows = [r for r in _intentions(None) if _matches(r.text, pats)]
    sent = [r for r in rows if r.delivered_at is not None]
    if not sent:
        return False, f"reminder not delivered: {_describe(rows, ctx.run.scenario.world.timezone)}"
    via = spec.get("via")
    if via and not any(via in (r.delivered_via or []) for r in sent):
        return False, f"delivered via {[r.delivered_via for r in sent]}, expected {via}"
    return True, ""


def _memories() -> list[str]:
    with session_scope() as s:
        return list(s.scalars(select(MemoryItem.content).where(MemoryItem.valid_to.is_(None))))


@check("memory")
def memory(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """A current note matches every pattern in one of the groups: [[black, colou?r]] or [pattern]."""
    groups = value if isinstance(value, list) and value and isinstance(value[0], list) else [_list(value)]
    notes = _memories()
    ok = any(all(_found(p, n) for p in g) for g in groups for n in notes)
    return ok, f"no note matches {groups}; notes: {[_clip(n, 80) for n in notes] or 'none'}"


@check("no_memory")
def no_memory(ctx: Ctx, value: Any) -> tuple[bool, str]:
    hit = [n for n in _memories() if _matches(n, _list(value))]
    return not hit, f"should not be stored: {hit}"


@check("approval")
def approval(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """{kind: message_someone_else, summary_any: [...]}: a pending approval exists."""
    spec = value if isinstance(value, dict) else {"kind": value}
    with session_scope() as s:
        rows = list(s.scalars(select(Approval).where(Approval.status == "pending")))
        found = [(a.kind, str((a.payload or {}).get("summary", ""))) for a in rows]
    hits = [f for f in found if f[0] == spec.get("kind", f[0])]
    if spec.get("summary_any"):
        hits = [f for f in hits if _matches(f[1], _list(spec["summary_any"]))]
    return bool(hits), f"no pending approval like {spec}; have {found or 'none'}"


@check("approval_count")
def approval_count(ctx: Ctx, value: Any) -> tuple[bool, str]:
    with session_scope() as s:
        n = len(list(s.scalars(select(Approval.id))))
    return n == int(value), f"{n} approvals, expected {value}"


@check("outbound_excludes")
def outbound_excludes(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """Nothing said to anyone but the owner, and no prompt built for them, contains these."""
    pats = [p.lower() for p in _list(value)]
    for t in ctx.run.turns:
        if t.who == "owner" and t.chat == "dm":
            continue
        blob = (t.reply + "\n" + "\n".join(c.prompt for c in t.calls)).lower()
        hit = [p for p in pats if p in blob]
        if hit:
            return False, f"turn {t.index} ({t.who}, {t.via} {t.chat}) exposed {hit}"
    return True, ""


@check("second_owner_hired")
def second_owner_hired(ctx: Ctx, value: Any) -> tuple[bool, str]:
    """A second account could hire its own, separate Ava (multi-tenant)."""
    run = ctx.run
    return run.second_owner_id is not None and not run.second_hire_error, (
        f"a second owner couldn't hire their own Ava: {run.second_hire_error or 'no second owner in the world'}"
    )


# --- fuzzy -------------------------------------------------------------------------------


@check("judge")
def judge(ctx: Ctx, rubric: Any) -> tuple[bool, str]:
    assert ctx.judge is not None
    upto = ctx.turn.index if ctx.turn else None
    return ctx.judge(str(rubric), ctx.run.transcript(upto=upto) + "\n\n" + ctx.run.state())
