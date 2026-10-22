"""The scenario file format (YAML), validated with pydantic.

A scenario is a short, scripted conversation with the owner, a colleague or a stranger on one
or more channels, at a chosen local time, plus checks graded in code (database state, tool calls,
citations, forbidden text) and, for fuzzy things only, by an LLM judge with a pass/fail rubric.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "evals" / "scenarios"

Category = Literal[
    "recall",
    "cross_channel",
    "reminders",
    "citations",
    "honesty",
    "privacy",
    "approvals",
    "briefing",
    "chitchat",
    "triage",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]
Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Due(Strict):
    """A moment relative to the scenario's start: `day` days after the start's local date at
    `at` (HH:MM, the owner's zone), or `minutes` after the start."""

    day: int | None = None
    at: str | None = None
    minutes: int | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Due:
        if (self.minutes is None) == (self.at is None):
            raise ValueError("give either `minutes` or `day` + `at`")
        return self


class SeedMemory(Strict):
    content: str
    audience: Literal["owner", "org", "public"] = "owner"
    category: str = "other"
    source: str = "chat"


class SeedReminder(Strict):
    text: str
    due: Due


class SeedMessage(Strict):
    """Something said earlier (logged, not answered), e.g. on Telegram the day before."""

    via: Literal["web", "telegram", "slack"] = "telegram"
    text: str


class World(Strict):
    timezone: str = "Asia/Kolkata"
    owner_name: str = "Shashank Rao"
    colleague: bool = True  # Priya Tan, linked on Slack
    second_owner: bool = False  # another account that hires its own Ava (multi-tenant)
    web_pages: dict[str, str] = Field(default_factory=dict)  # url → page text; web_fetch reads these
    telegram_linked: bool = True
    interrupt_channel: Literal["app", "telegram", "slack"] = "app"
    digest: Literal["morning", "evening", "both", "none"] = "morning"
    shareable: list[str] = Field(default_factory=list)
    never_share: str = ""
    memories: list[SeedMemory] = Field(default_factory=list)
    reminders: list[SeedReminder] = Field(default_factory=list)
    messages: list[SeedMessage] = Field(default_factory=list)
    contacts: list[dict[str, Any]] = Field(default_factory=list)  # {name, relationship, vip}
    # Triage (ADR 0010). Off unless a scenario turns it on; then two more people are linked on Slack
    # and Telegram: Dana Lee, a VIP contact (`who: vip`), and Sam Park, a contact (`who: contact`).
    triage_enabled: bool = False
    stage: Literal["infant", "child", "adolescent", "adult"] | None = None
    urgent_topics: list[str] = Field(default_factory=list)
    quiet_hours: str = ""  # "22:00-07:00", the owner's zone; "" keeps the default
    vip_breaks_quiet: bool = True
    away_days: int = 0  # away from the start, for this many days
    away_visibility: Literal["everyone", "colleagues", "nobody"] = "colleagues"
    stranger_intake: bool = False
    triage_settings: dict[str, Any] = Field(default_factory=dict)  # any other triage_settings column
    # Offline, the triage classifier isn't connected (rules only). True: it is, and answers with each
    # turn's `fake.triage` (a TriageOutput as a dict, or "budget" for a refusal).
    triage_model: bool = False


class FakeTool(Strict):
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class FakeTurn(Strict):
    """What the offline model does on this turn: tool calls, then a reply. Lets offline runs
    grade model-path checks, which proves the harness and the checks themselves work."""

    tools: list[FakeTool] = Field(default_factory=list)
    reply: str = "OK."
    triage: dict[str, Any] | Literal["budget"] | None = None  # the triage classifier's answer (world.triage_model)


class Press(Strict):
    """The owner presses a button on the latest escalation (TR4 will bring Telegram and Slack buttons;
    until then the press is applied as it would arrive)."""

    # should_have_told: the owner's feedback on the latest held item (from the Needs-you inbox)
    verb: Literal["ack", "reply", "call", "not_urgent", "snooze_30m", "snooze_2h", "done", "should_have_told"] = "ack"
    via: Literal["app", "telegram", "slack"] = "app"
    id: str = ""  # the press's own id: the same id twice is one press
    by: Literal["owner", "colleague"] = "owner"  # anyone but the owner is refused (and audited)


class Turn(Strict):
    who: Literal["owner", "colleague", "stranger", "second_owner", "vip", "contact"] = "owner"
    via: Literal["web", "telegram", "slack"] = "web"
    chat: Literal["dm", "group"] = "dm"
    say: str | None = None
    advance: str | None = None  # move the clock first: "+90m", "+2h", "+1d"
    run_jobs: bool = True  # run due background jobs (learning, reminders) after the turn
    same_id_as: int | None = None  # a redelivery: reuse that turn's platform message id
    digest: Literal["morning", "evening"] | None = None  # compose the digest instead of saying something
    expect: list[dict[str, Any]] = Field(default_factory=list)
    fake: FakeTurn | None = None
    press: Press | None = None

    @model_validator(mode="after")
    def _something(self) -> Turn:
        if self.say is None and self.digest is None and self.advance is None and self.press is None:
            raise ValueError("a turn needs `say`, `digest`, `press` or `advance`")
        return self


class Scenario(Strict):
    id: str
    category: Category
    severity: Severity = "medium"
    title: str
    based_on: str = ""  # the real failure this guards against, if any
    known_gap: str = ""  # an accepted, already-tracked gap (e.g. an ADR work package)
    clock: str = "10:00"  # local start time (owner's zone), the next occurrence after now
    weekday: str | None = None  # optionally the next such weekday, e.g. "friday"
    world: World = Field(default_factory=World)
    turns: list[Turn]
    checks: list[dict[str, Any]] = Field(default_factory=list)  # graded after the last turn

    @model_validator(mode="after")
    def _check_names(self) -> Scenario:
        from superaide.evals.checks import KNOWN

        for where, items in [("checks", self.checks), *[(f"turn {i + 1}", t.expect) for i, t in enumerate(self.turns)]]:
            for item in items:
                if len(item) != 1:
                    raise ValueError(f"{where}: each check is a one-key mapping, got {sorted(item)}")
                (name,) = item
                if name not in KNOWN:
                    raise ValueError(f"{where}: unknown check {name!r}")
        return self


def load_all(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    out: list[Scenario] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        docs = yaml.safe_load(path.read_text())
        for raw in docs if isinstance(docs, list) else [docs]:
            try:
                sc = Scenario.model_validate(raw)
            except Exception as exc:
                raise ValueError(f"{path.name}: {exc}") from exc
            if sc.id in seen:
                raise ValueError(f"{path.name}: duplicate scenario id {sc.id!r}")
            seen.add(sc.id)
            out.append(sc)
    return out
