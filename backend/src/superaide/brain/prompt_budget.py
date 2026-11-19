"""Fit a prompt into the model's input budget by rule, and say what was dropped (#641).

Before each model call the loop asks for the prompt to fit `context_window.input_budget`. What does not fit is
dropped in a fixed order, by code, never by the model:

1. older history, oldest first, until only the last four turns are left;
2. recalled notes that were attached to the latest message, lowest ranked first (the `recall` tool can fetch more);
3. the last four turns of history, oldest first;
4. long results of tools already run this turn, cut to their first lines.

Never touched: the system blocks (charter, safety rules, who is speaking, audience and trust lines, pending state),
the tool definitions, and the latest message. Nothing is ever added, so a smaller prompt can lose information but
cannot show a person anything they could not see in the full one. The audit row names counts only, no text.
If the fixed parts alone are over the budget the rest is still dropped and the excess is recorded
(`over_budget_by`); the smaller tiers (compact, minimal) are what make the fixed parts smaller.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from superaide.brain import context_window as cw
from superaide.platform.audit import audit

log = logging.getLogger(__name__)

KEEP_TURNS = 4  # the last four turns (user and reply) are the last history to go
TOOL_RESULT_KEEP_CHARS = 600
TOOL_RESULT_MIN_CHARS = 1_500  # a tool result shorter than this is never cut
TRIMMED = "\n[cut to fit the model's window]"

Msg = dict[str, Any]


@dataclass
class Trim:
    budget: int
    before: int
    after: int
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.dropped)

    @property
    def over_budget_by(self) -> int:
        return max(0, self.after - self.budget)


def _tokens(obj: Any) -> int:
    return math.ceil(len(json.dumps(obj, default=str, ensure_ascii=False)) / cw.CHARS_PER_TOKEN)


def _fixed(system: Any, tools: Any) -> int:
    return cw.estimate_request(system, [], tools)


def _units(history: list[Msg]) -> list[list[Msg]]:
    """History as units that start at a user message, so a cut never leaves a reply with no question."""
    units: list[list[Msg]] = []
    for m in history:
        if m.get("role") == "user" or not units:
            units.append([m])
        else:
            units[-1].append(m)
    return units


def _is_note_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "search_result"


def fit(system: Any, tools: Any, msgs: list[Msg], turn_start: int, budget: int) -> tuple[list[Msg], int, Trim]:
    """`msgs[:turn_start]` is history, `msgs[turn_start]` the latest message (with any attached notes), and what
    follows is this turn's own tool exchange. Returns the fitted messages, the new `turn_start` and what was done."""
    fixed = _fixed(system, tools)
    total = fixed + _tokens(msgs)
    trim = Trim(budget=budget, before=total, after=total)
    if total <= budget or not msgs:
        return msgs, turn_start, trim
    history = list(msgs[:turn_start])
    rest = [
        {**m, "content": list(m["content"])} if isinstance(m.get("content"), list) else dict(m)
        for m in msgs[turn_start:]
    ]

    def count(kind: str, n: int = 1) -> None:
        trim.dropped[kind] = trim.dropped.get(kind, 0) + n

    def now() -> int:
        return fixed + _tokens(history) + _tokens(rest)

    def drop_history(keep_messages: int) -> None:
        units = _units(history)
        while units and sum(len(u) for u in units) > keep_messages and now() > budget:
            gone = units.pop(0)
            history[: len(gone)] = []
            count("history_messages", len(gone))

    drop_history(KEEP_TURNS * 2)
    if now() > budget and rest and isinstance(rest[0].get("content"), list):
        blocks = list(rest[0]["content"])
        while now() > budget and any(_is_note_block(b) for b in blocks):
            last = max(i for i, b in enumerate(blocks) if _is_note_block(b))
            del blocks[last]  # the notes come best first, so the last one goes first
            count("recalled_notes")
            rest[0] = {**rest[0], "content": blocks}
        if not any(_is_note_block(b) for b in blocks) and trim.dropped.get("recalled_notes"):
            blocks = [
                b for b in blocks if not (isinstance(b, dict) and str(b.get("text", "")).startswith("(Notes from"))
            ]
            rest[0] = {**rest[0], "content": blocks}
    drop_history(0)
    if now() > budget:
        for m in sorted(rest[1:], key=_tokens, reverse=True):
            if now() <= budget:
                break
            if _cut_results(m):
                count("tool_results")
    trim.after = now()
    return [*history, *rest], len(history), trim


def _cut_results(m: Msg) -> bool:
    """Shorten the long text of tool results in a message. True when something was cut."""
    if not isinstance(m.get("content"), list):
        return False
    cut = False
    for i, block in enumerate(m["content"]):
        if not (isinstance(block, dict) and block.get("type") == "tool_result"):
            continue
        body = block.get("content")
        if isinstance(body, str) and len(body) > TOOL_RESULT_MIN_CHARS:
            m["content"][i] = {**block, "content": body[:TOOL_RESULT_KEEP_CHARS] + TRIMMED}
            cut = True
    return cut


class TurnBudget:
    """One turn's budget: the window is looked up once, each model call is fitted, the audit row says what went."""

    def __init__(self, session: Session, model_id: str, max_tokens: int) -> None:
        self.session, self.model_id, self.max_tokens = session, model_id, max_tokens
        self.budget: int | None = None
        self.window: cw.WindowInfo | None = None

    def _resolve(self) -> int:
        if self.budget is None:
            from superaide.brain.context_watch import window_for
            from superaide.platform.config import get_settings

            self.window = window_for(
                self.session, self.model_id, probe=getattr(get_settings(), "deploy_mode", "") != "hosted"
            )
            self.budget = cw.input_budget(self.window.tokens, self.max_tokens)
        return self.budget

    def window_info(self) -> cw.WindowInfo:
        self._resolve()
        assert self.window is not None
        return self.window

    def fit(self, system: Any, tools: Any, msgs: list[Msg], turn_start: int) -> tuple[list[Msg], int]:
        """Never raises: on any failure the prompt goes out as it was."""
        try:
            fitted, start, trim = fit(system, tools, msgs, turn_start, self._resolve())
            if trim.changed:
                assert self.window is not None
                audit(
                    self.session,
                    "brain",
                    "prompt_trimmed",
                    model=self.model_id,
                    window=self.window.tokens,
                    window_source=self.window.source,
                    budget=trim.budget,
                    before=trim.before,
                    after=trim.after,
                    over_budget_by=trim.over_budget_by,
                    dropped=trim.dropped,
                )
            return fitted, start
        except Exception:
            log.exception("couldn't fit the prompt to the window")
            return msgs, turn_start
