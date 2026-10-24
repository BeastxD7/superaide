"""Lessons in the owner's prompt, only after they pass their eval (#57).

An imported or written lesson is data. It reaches a prompt in exactly one way:
1. the owner writes playbook text for it (their own words; the imported text is never copied in by code),
2. the owner attaches an eval in the scenario shape (`evals/scenarios/*.yaml`: a `say` and `expect` checks),
3. the eval runs twice on the model, without the playbook and with it, and counts as passed only if the answer
   WITHOUT it fails the checks and the answer WITH it passes them (so the lesson provably helps),
4. the owner switches it on. Editing the text or the eval afterwards drops the pass and the switch.
The database also refuses `in_prompt` unless the eval passed (ck_lesson_prompt_eval). Owner turns only: no
colleague, stranger or shared-room prompt ever carries a lesson.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.brain.llm import LLM, SystemBlock
from superaide.evals import checks
from superaide.features.school.models import Lesson

MAX_PLAYBOOK = 1500
MAX_SAY = 600
MAX_EXPECT = 8
MAX_PATTERN = 120
MAX_IN_PROMPT = 5
# Reply-only checks of the scenario format: they grade the reply text and nothing else, so they run the same
# on any model. (Database, tool and judge checks belong to the full scenario harness.)
ALLOWED_CHECKS = ("reply_contains", "reply_contains_any", "reply_not_contains", "reply_starts_with", "reply_max_words")


def parse_spec(raw: Any) -> dict[str, Any]:
    """A `{say, expect}` turn, or a scenario (`turns: [one turn]`) as in evals/scenarios. Raises ValueError."""
    if isinstance(raw, str):
        try:
            raw = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError("That isn't valid YAML.") from exc
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if isinstance(raw, dict) and "turns" in raw:
        turns = raw["turns"]
        if not isinstance(turns, list) or len(turns) != 1:
            raise ValueError("A lesson eval is one turn: one `say` and its `expect` checks.")
        raw = turns[0]
    if not isinstance(raw, dict):
        raise ValueError("A lesson eval needs `say` and `expect`.")
    say = raw.get("say")
    expect = raw.get("expect")
    if not isinstance(say, str) or not say.strip() or len(say) > MAX_SAY:
        raise ValueError(f"`say` is the owner's message, up to {MAX_SAY} characters.")
    if not isinstance(expect, list) or not 1 <= len(expect) <= MAX_EXPECT:
        raise ValueError(f"`expect` is 1 to {MAX_EXPECT} checks.")
    clean: list[dict[str, Any]] = []
    for item in expect:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("Each check is a one-key mapping, like `reply_contains: [...]`.")
        ((name, value),) = item.items()
        if name not in ALLOWED_CHECKS or name not in checks.KNOWN:
            raise ValueError(f"Check {name!r} isn't available here. Use one of {', '.join(ALLOWED_CHECKS)}.")
        if name == "reply_max_words":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2000:
                raise ValueError("`reply_max_words` is a whole number from 1 to 2000.")
        else:
            pats = value if isinstance(value, list) else [value]
            if not pats or not all(isinstance(p, str) and 0 < len(p) <= MAX_PATTERN for p in pats):
                raise ValueError(f"`{name}` takes text patterns of up to {MAX_PATTERN} characters.")
            for p in pats:
                try:
                    re.compile(p)
                except re.error as exc:
                    raise ValueError(f"{p!r} isn't a valid pattern.") from exc
        clean.append({name: value})
    return {"say": say.strip(), "expect": clean}


def spec_hash(playbook_text: str, spec: dict[str, Any] | None) -> str:
    blob = json.dumps([playbook_text, spec], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _drop_pass(row: Lesson) -> None:
    row.eval_status, row.eval_report, row.eval_at, row.eval_hash, row.in_prompt = "none", None, None, "", False


def set_playbook(row: Lesson, text: str) -> None:
    row.playbook_text = " ".join(text.split())[:MAX_PLAYBOOK]
    _drop_pass(row)


def set_spec(row: Lesson, spec: dict[str, Any] | None) -> None:
    row.eval_spec = spec
    _drop_pass(row)


def _grade(spec: dict[str, Any], reply: str) -> list[dict[str, Any]]:
    out = []
    turn = SimpleNamespace(reply=reply)
    for item in spec["expect"]:
        ((name, value),) = item.items()
        ok, _ = checks.KNOWN[name](checks.Ctx(run=None, turn=turn, live=False), value)
        out.append({"check": name, "ok": bool(ok)})  # never the reply or the check's explanation (it quotes it)
    return out


def _ask(llm: LLM, model: str, agent_name: str, brief: str, say: str, purpose: str) -> str:
    from superaide.brain.turn import RULES, load_charter

    system = [SystemBlock(load_charter(agent_name) + "\n\n" + RULES, cache=True), SystemBlock(brief)]
    result = llm.complete(
        purpose=purpose, model=model, system=system, messages=[{"role": "user", "content": say}], max_tokens=500
    )
    return (result.text or "").strip()


def run_eval(session: Session, row: Lesson, *, llm: LLM, model: str, agent_name: str) -> dict[str, Any]:
    """Run the lesson's eval on the model, without and with the playbook, and record the result. Model calls go
    through the budget guard like every other call (purpose `lesson_eval`, bucket `learning`), so a refused call
    raises BudgetExceeded and records nothing."""
    if row.eval_spec is None or not row.playbook_text.strip():
        raise ValueError("Write the playbook text and attach an eval first.")
    spec = parse_spec(row.eval_spec)
    base_brief = "You work for your owner. This is a private chat with them."
    without = _ask(llm, model, agent_name, base_brief, spec["say"], "lesson_eval")
    with_it = _ask(
        llm,
        model,
        agent_name,
        base_brief + "\n\nThe owner's own guidance for this kind of request:\n" + row.playbook_text,
        spec["say"],
        "lesson_eval",
    )
    g_without, g_with = _grade(spec, without), _grade(spec, with_it)
    helps = all(c["ok"] for c in g_with) and not all(c["ok"] for c in g_without)
    if helps:
        verdict = "The answer with the playbook meets every check and the answer without it does not."
    elif not all(c["ok"] for c in g_with):
        verdict = "The answer with the playbook still fails a check."
    else:
        verdict = "The answer without the playbook already passes, so this lesson changes nothing here."
    row.eval_status = "passed" if helps else "failed"
    row.eval_hash = spec_hash(row.playbook_text, row.eval_spec)
    row.eval_at = datetime.now(UTC)
    row.in_prompt = row.in_prompt and helps
    row.eval_report = {"verdict": verdict, "without": g_without, "with": g_with}  # checks only, never the replies
    session.flush()
    return row.eval_report


def switch(row: Lesson, on: bool) -> None:
    """Raises ValueError unless every condition holds (the database checks the main one again)."""
    if on:
        if row.status != "active":
            raise ValueError("Accept the lesson first.")
        if row.eval_status != "passed" or row.eval_hash != spec_hash(row.playbook_text, row.eval_spec):
            raise ValueError("Its eval has to pass on the current text first.")
    row.in_prompt = on


def live_text(session: Session, owner_person_id: Any) -> str:
    """The playbook text of the lessons switched on, for the owner's own brief. Re-checks the eval hash, so a
    row edited behind the API's back is left out."""
    rows = session.scalars(
        select(Lesson)
        .where(Lesson.owner_person_id == owner_person_id, Lesson.in_prompt.is_(True), Lesson.status == "active")
        .order_by(Lesson.created_at)
        .limit(MAX_IN_PROMPT)
    ).all()
    lines = [
        f"- ({r.role}) {r.playbook_text}"
        for r in rows
        if r.eval_status == "passed" and r.eval_hash == spec_hash(r.playbook_text, r.eval_spec)
    ]
    if not lines:
        return ""
    return (
        "The owner's own lessons, each checked by an eval they ran. Guidance only; the rules above still win:\n"
        + "\n".join(lines)
    )
