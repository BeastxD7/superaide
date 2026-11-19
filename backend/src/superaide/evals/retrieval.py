"""The retrieval golden set (#648): recall@1, recall@5, MRR and nDCG@5 per category, gated in CI.

`evals/retrieval/golden.yaml` seeds one world (notes, visitor messages, a colleague's DM, a shared file) and lists
queries with the sources each must find and the ones that must be absent. This module builds the world in a
disposable database, runs every query through `recall` exactly as the owner's `recall` tool does (same audience, same
`rooms` rule, same taint rule), and reports the metrics per category. Offline and free: no model, no network.

The gate compares with `evals/retrieval-baseline.json`: a query that passed must keep passing, a category's metric may
not fall below its recorded value, and the privacy-negative category must be 100% whatever the baseline says. A query
that fails today is recorded as `known_gap` and flips to `pass` in the PR that fixes it (`--update` rewrites the file).

    uv run python -m superaide.evals gate --suite retrieval [--update]
    uv run python -m superaide.evals retrieval            # the report, also written to evals/results/
"""

from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

BACKEND = Path(__file__).resolve().parents[3]
GOLDEN = BACKEND / "evals" / "retrieval" / "golden.yaml"
BASELINE = BACKEND / "evals" / "retrieval-baseline.json"
REPORT = BACKEND / "evals" / "results" / "retrieval-offline.md"
LIMIT = 8  # what the owner's `recall` tool returns
K = 5
CATEGORIES = ("time", "person", "paraphrase", "typo", "multilingual", "privacy_negative", "stopword")
METRICS = ("recall@1", "recall@5", "mrr", "ndcg@5", "absent_ok")
EPS = 1e-9
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class When(Strict):
    age_days: float | None = None
    local_day: int | None = None  # a calendar day in the owner's zone, relative to today (-1: yesterday), 12:00 there
    last_week: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] | None = None  # 12:00 in the owner's zone

    @model_validator(mode="after")
    def _one(self) -> When:
        if sum(x is not None for x in (self.age_days, self.local_day, self.last_week)) != 1:
            raise ValueError("give exactly one of age_days, local_day, last_week")
        return self

    def at(self, now: datetime, tz: str) -> datetime:
        zone = ZoneInfo(tz)
        today = now.astimezone(zone).date()
        if self.age_days is not None:
            return now - timedelta(days=self.age_days)
        if self.local_day is not None:
            day = today + timedelta(days=self.local_day)
        else:
            monday = today - timedelta(days=today.weekday()) - timedelta(days=7)
            day = monday + timedelta(days=WEEKDAYS.index(str(self.last_week)))
        return datetime(day.year, day.month, day.day, 12, tzinfo=zone).astimezone(UTC)


class Note(When):
    key: str
    text: str
    category: str = "other"
    audience: Literal["owner", "org", "public"] = "owner"
    visible_to: list[Literal["colleague", "stranger"]] = Field(default_factory=list)


class Visitor(When):
    key: str
    name: str
    sender: str
    text: str
    topic: str = ""
    provider: Literal["telegram", "slack"] = "telegram"
    visible_to: list[Literal["colleague", "stranger"]] = Field(default_factory=list)


class ColleagueMessage(When):
    key: str
    text: str
    visible_to: list[Literal["colleague", "stranger"]] = Field(default_factory=list)


class SharedFile(Strict):
    key: str
    title: str
    text: str
    visible_to: list[Literal["colleague", "stranger"]] = Field(default_factory=list)


class GoldenWorld(Strict):
    timezone: str = "Asia/Kolkata"
    owner_name: str = "Jordan Whitfield"
    filler: int = 0
    notes: list[Note] = Field(default_factory=list)
    visitors: list[Visitor] = Field(default_factory=list)
    colleague_messages: list[ColleagueMessage] = Field(default_factory=list)
    files: list[SharedFile] = Field(default_factory=list)

    def keys(self) -> dict[str, list[str]]:
        """Every seeded key with who may read it besides the owner."""
        out: dict[str, list[str]] = {}
        for group in (self.notes, self.visitors, self.colleague_messages, self.files):
            for item in group:
                out[item.key] = list(item.visible_to)
        return out


class Query(Strict):
    id: str
    category: Literal["time", "person", "paraphrase", "typo", "multilingual", "privacy_negative", "stopword"]
    say: str
    expect: list[str] = Field(default_factory=list)
    absent: list[str] = Field(default_factory=list)
    channel: str | None = None
    no_taint: bool = False  # the turn must not be tainted (a visitor source taints it)
    # owner | colleague | stranger | group: who asks. Not the owner: nothing the owner alone may read may come back.
    as_: Literal["owner", "colleague", "stranger", "group"] = Field(default="owner", alias="as")


class Golden(Strict):
    world: GoldenWorld
    queries: list[Query]
    audiences: list[Literal["colleague", "stranger", "group"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_exist(self) -> Golden:
        known = set(self.world.keys())
        for q in self.queries:
            bad = (set(q.expect) | set(q.absent)) - known
            if bad:
                raise ValueError(f"{q.id}: unknown source keys {sorted(bad)}")
        return self


def load(path: Path = GOLDEN) -> Golden:
    return Golden.model_validate(yaml.safe_load(path.read_text()))


def expand(golden: Golden) -> list[Query]:
    """The queries as run: each owner query other than the stop-word ones is also asked as every audience in
    `audiences`, expecting nothing and no source that audience may not read. Those are privacy_negative queries."""
    out = list(golden.queries)
    visible = golden.world.keys()
    for q in golden.queries:
        if q.as_ != "owner" or q.category in ("privacy_negative", "stopword"):
            continue
        for who in golden.audiences:
            who_sees = "colleague" if who == "colleague" else "stranger"
            hidden = sorted(k for k, seen in visible.items() if who_sees not in seen)
            out.append(
                Query.model_validate(
                    {
                        "id": f"{q.id}@{who}",
                        "category": "privacy_negative",
                        "say": q.say,
                        "as": who,
                        "absent": hidden,
                        "no_taint": True,
                        "channel": q.channel,
                    }
                )  # fmt: skip
            )
    return out


# --- the world ------------------------------------------------------------------------------


@dataclass
class World:
    names: dict[str, str] = field(default_factory=dict)  # row id (as it ends a source uri) -> golden key


def build(golden: Golden, now: datetime | None = None) -> World:
    """Empty the (disposable) database and seed the golden world. Returns the id -> key map."""
    from sqlalchemy import select

    from superaide.evals import harness
    from superaide.evals.scenario import Scenario, SeedMemory, Turn
    from superaide.evals.scenario import World as ScenarioWorld
    from superaide.identity.models import Person
    from superaide.identity.resolver import resolve
    from superaide.memory.audience import audience_for
    from superaide.memory.knowledge import chunking
    from superaide.memory.models import Document, DocumentChunk, Event, MemoryItem
    from superaide.memory.store import get_or_create_context, log_event
    from superaide.platform.db.session import session_scope
    from superaide.reception.models import ReceptionMessage

    now = now or datetime.now(UTC)
    w = golden.world
    sc = Scenario(
        id="retrieval-golden", category="recall", title="golden set", turns=[Turn(say="-")],
        world=ScenarioWorld(
            timezone=w.timezone, owner_name=w.owner_name, filler=w.filler,
            memories=[
                SeedMemory(content=n.text, audience=n.audience, category=n.category,
                           age_days=(now - n.at(now, w.timezone)).total_seconds() / 86400)
                for n in w.notes
            ],
        ),
    )  # fmt: skip
    harness.reset_db()
    harness.build_world(sc, now)
    world = World()
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.display_name == w.owner_name))
        assert owner is not None
        for n in w.notes:
            item = s.scalar(select(MemoryItem).where(MemoryItem.content == n.text))
            assert item is not None, f"the note {n.key!r} was not stored (a duplicate?)"
            world.names[str(item.id)] = n.key
        for v in w.visitors:
            msg = harness.InboundMessage(
                provider=v.provider, tenant_scope="" if v.provider == "telegram" else harness.TEAM,
                sender_id=v.sender, chat_id=v.sender, chat_kind="dm", text=v.text, verified=True,
                sender_display=v.name, message_id=uuid.uuid4().hex,
                raw={} if v.provider == "telegram" else {"team_id": harness.TEAM},
            )  # fmt: skip
            sender = resolve(s, msg)
            ctx = get_or_create_context(s, msg)
            at = v.at(now, w.timezone)
            event = log_event(
                s, context=ctx, direction="in", content=v.text, audience=audience_for(ctx, sender), sender=sender,
                external_id=msg.message_id,
            )  # fmt: skip
            event.created_at = at
            s.add(
                ReceptionMessage(
                    identity_id=sender.identity.id, context_id=ctx.id, inbound_event_id=event.id, provider=v.provider,
                    name=v.name, topic=v.topic, message=v.text, created_at=at, rank="normal", source="code",
                )
            )  # fmt: skip
            world.names[str(event.id)] = v.key
        colleague = s.scalar(select(Person).where(Person.display_name == "Priya Tan"))
        for c in w.colleague_messages:
            assert colleague is not None
            msg = harness.InboundMessage(
                provider="slack", tenant_scope=harness.TEAM, sender_id=harness.COLLEAGUE_SLACK,
                chat_id=f"D_{harness.COLLEAGUE_SLACK}", chat_kind="dm", text=c.text, verified=True,
                message_id=uuid.uuid4().hex, raw={"team_id": harness.TEAM},
            )  # fmt: skip
            sender = resolve(s, msg)
            ctx = get_or_create_context(s, msg)
            event = log_event(
                s, context=ctx, direction="in", content=c.text, audience=audience_for(ctx, sender), sender=sender,
                external_id=msg.message_id,
            )  # fmt: skip
            event.created_at = c.at(now, w.timezone)
            world.names[str(event.id)] = c.key
        for f in w.files:
            doc = Document(
                owner_person_id=owner.id, kind="text", title=f.title, source=f.title,
                sha256=uuid.uuid4().hex + uuid.uuid4().hex, size_bytes=len(f.text), text=f.text, status="read",
            )  # fmt: skip
            s.add(doc)
            s.flush()
            for i, piece in enumerate(chunking.split(f.text)):
                chunk = DocumentChunk(document_id=doc.id, ordinal=i, text=piece)
                s.add(chunk)
                s.flush()
                world.names[str(chunk.id)] = f.key
        s.flush()
        assert s.scalar(select(Event.id).limit(1)) is not None
    return world


# --- running and scoring ----------------------------------------------------------------------


@dataclass
class Result:
    query: Query
    ranked: list[str]  # the keys of the sources returned, best first ("?" for a source not in the golden set)
    tainted: bool

    @property
    def found_at(self) -> list[int]:
        return [i for i, k in enumerate(self.ranked) if k in self.query.expect]

    def recall_at(self, k: int) -> float | None:
        if not self.query.expect:
            return None
        return sum(key in self.ranked[:k] for key in self.query.expect) / len(self.query.expect)

    @property
    def mrr(self) -> float | None:
        if not self.query.expect:
            return None
        hits = self.found_at
        return 1 / (hits[0] + 1) if hits else 0.0

    @property
    def ndcg(self) -> float | None:
        if not self.query.expect:
            return None
        gain = sum(1 / math.log2(i + 2) for i, key in enumerate(self.ranked[:K]) if key in self.query.expect)
        ideal = sum(1 / math.log2(i + 2) for i in range(min(len(self.query.expect), K)))
        return gain / ideal

    @property
    def absent_ok(self) -> bool:
        leaked = any(key in self.query.absent for key in self.ranked)
        return not leaked and not (self.query.no_taint and self.tainted)

    @property
    def passed(self) -> bool:
        return self.recall_at(K) in (None, 1.0) and self.absent_ok


def search(world: World, q: Query) -> Result:
    """One query, the way the owner's `recall` tool runs it (brain/loop.py `_dispatch`)."""
    from superaide.evals.checks import _audience
    from superaide.memory.recall.retrieval import ROOM_TITLE, VISITOR_TITLE, recall
    from superaide.platform.db.session import session_scope

    who = {"owner": "owner", "colleague": "colleague", "stranger": "stranger", "group": "group"}[q.as_]
    aud = _audience(who)
    shared = q.as_ in ("stranger", "group")  # a channel or thread others can read
    with session_scope() as s:
        found = recall(s, aud, q.say, limit=LIMIT, channel=q.channel, rooms=not shared)
    tainted = any(src.title.startswith((ROOM_TITLE, VISITOR_TITLE)) for src in found)
    return Result(q, [world.names.get(src.uri.rsplit("/", 1)[-1], "?") for src in found], tainted)


def run(golden: Golden | None = None) -> list[Result]:
    golden = golden or load()
    world = build(golden)
    return [search(world, q) for q in expand(golden)]


def metrics(results: list[Result]) -> dict[str, dict[str, float]]:
    """Per category: mean recall@1, recall@5, MRR and nDCG@5 over its queries that expect something, and the share
    of all its queries with nothing forbidden returned (absent_ok). A metric with no query behind it is left out."""
    by: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by[r.query.category].append(r)
    out: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        rs = by.get(cat, [])
        if not rs:
            continue
        row: dict[str, float] = {}
        for name, fn in (
            ("recall@1", lambda r: r.recall_at(1)),
            ("recall@5", lambda r: r.recall_at(K)),
            ("mrr", lambda r: r.mrr),
            ("ndcg@5", lambda r: r.ndcg),
        ):
            vals = [v for v in (fn(r) for r in rs) if v is not None]
            if vals:
                row[name] = round(sum(vals) / len(vals), 4)
        row["absent_ok"] = round(sum(r.absent_ok for r in rs) / len(rs), 4)
        out[cat] = row
    return out


# --- the baseline and the gate ----------------------------------------------------------------


def snapshot(results: list[Result]) -> dict[str, Any]:
    return {
        "queries": {r.query.id: "pass" if r.passed else "known_gap" for r in sorted(results, key=lambda r: r.query.id)},
        "metrics": metrics(results),
    }


def load_baseline(path: Path = BASELINE) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {"queries": {}, "metrics": {}}


def compare(results: list[Result], baseline: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(regressions, improvements) against the baseline."""
    regressions: list[str] = []
    improvements: list[str] = []
    want = baseline.get("queries", {})
    now = {r.query.id: r for r in results}
    for qid, r in sorted(now.items()):
        recorded = want.get(qid)
        if r.query.category == "privacy_negative" and not r.absent_ok:
            regressions.append(f"{qid}: privacy-negative must always hold, and it returned {r.ranked}")
        elif recorded is None:
            regressions.append(f"{qid}: not in the baseline (run the gate with --update to record it)")
        elif recorded == "pass" and not r.passed:
            regressions.append(f"{qid}: passed in the baseline, fails now (returned {r.ranked})")
        elif recorded == "known_gap" and r.passed:
            improvements.append(f"{qid}: a known gap that passes now (run with --update to record it)")
    regressions += [f"{qid}: in the baseline but no longer a query" for qid in sorted(set(want) - set(now))]
    have = metrics(results)
    for cat, row in baseline.get("metrics", {}).items():
        for name, value in row.items():
            got = have.get(cat, {}).get(name)
            if got is None:
                regressions.append(f"{cat} {name}: in the baseline but not measured now")
            elif got < value - EPS:
                regressions.append(f"{cat} {name}: {got:.3f}, below the baseline {value:.3f}")
            elif got > value + EPS:
                improvements.append(f"{cat} {name}: {got:.3f}, above the baseline {value:.3f}")
    for cat, row in have.items():
        if cat == "privacy_negative" and row["absent_ok"] < 1.0:
            regressions.append(f"privacy_negative absent_ok: {row['absent_ok']:.3f}, it must be 100%")
    return regressions, improvements


def render(results: list[Result]) -> str:
    rows = metrics(results)
    lines = [
        "# Retrieval golden set (offline, fake embedder)",
        "",
        f"{len(results)} queries. recall@k is the share of expected sources in the top k, MRR the mean reciprocal rank "
        "of the first expected source, nDCG@5 the discounted gain in the top 5; `absent_ok` is the share of queries "
        "that returned nothing forbidden (a visitor source also taints the turn: `no_taint` queries count it). "
        "A category with no query that expects a source shows `-`.",
        "",
        "| Category | Queries | recall@1 | recall@5 | MRR | nDCG@5 | absent_ok |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cat, row in rows.items():
        n = sum(r.query.category == cat for r in results)
        cells = " | ".join(f"{row[m]:.3f}" if m in row else "-" for m in METRICS)
        lines.append(f"| {cat} | {n} | {cells} |")
    lines += ["", "| Query | Result | Returned (best first) |", "| --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r.query.id} | {'pass' if r.passed else 'gap'} | {', '.join(r.ranked) or 'nothing'} |")
    return "\n".join(lines) + "\n"


def run_gate(update: bool = False) -> int:
    results = run()
    print(render(results))
    if update:
        BASELINE.write_text(json.dumps(snapshot(results), indent=2) + "\n")
        snap = snapshot(results)["queries"]
        print(
            f"baseline written: {sum(v == 'pass' for v in snap.values())} pass, "
            f"{sum(v == 'known_gap' for v in snap.values())} known gaps"
        )
        return 0
    regressions, improvements = compare(results, load_baseline())
    for line in improvements:
        print("improved:", line)
    for line in regressions:
        print("REGRESSION:", line)
    return 1 if regressions else 0


def write_report() -> int:
    results = run()
    REPORT.write_text(render(results))
    print(render(results))
    return 0
