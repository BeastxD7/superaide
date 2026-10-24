"""Private Lobe boundary (#128): the cloud may ask a small local model only typed questions, and only typed
answers come back. This is the pure-code gate; the local model and the device RPC are not built.

The rule, kept in code: an answer that crosses the boundary is a boolean, one value from a fixed list, or a
whole number inside a stated range. Nothing else passes: not a sentence, a name, a number outside the range, a
list, an object, an extra field, or an enum value that is not exactly one of the listed ones. The question
itself comes from the registry below, never from the model, so the cloud cannot phrase a leading question or
widen the list of answers. Anything doubtful is refused (fail closed); a refused answer reveals nothing but
the reason code.

The questions here are examples of the shape; the owner's real list (which facts may be asked about at all)
is a product decision, and nothing registers a question except code in this file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MAX_ENUM_VALUES = 12
MAX_ENUM_LENGTH = 24


class LobeError(ValueError):
    """The answer is not a typed answer. `code` is all a caller may log or return: never the offending value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Question:
    id: str
    kind: str  # "bool" | "enum" | "range"
    choices: tuple[str, ...] = ()
    low: int = 0
    high: int = 0

    def __post_init__(self) -> None:
        if self.kind not in {"bool", "enum", "range"}:
            raise ValueError(f"unknown kind {self.kind!r}")
        if self.kind == "enum":
            ok = 2 <= len(self.choices) <= MAX_ENUM_VALUES and len(set(self.choices)) == len(self.choices)
            if not ok or not all(
                c.isascii() and c.replace("_", "").isalnum() and len(c) <= MAX_ENUM_LENGTH for c in self.choices
            ):
                raise ValueError("an enum needs 2 to 12 distinct short a-z0-9_ values")
        if self.kind == "range" and not (self.low < self.high and self.high - self.low <= 1000):
            raise ValueError("a range needs low < high, at most 1000 apart")


# What the cloud may ask. Each answer is one of the three typed shapes; none can carry a name or a sentence.
REGISTRY: dict[str, Question] = {
    q.id: q
    for q in (
        Question("calendar_blocked_next_tuesday", "bool"),
        Question("health_appointment_this_week", "bool"),
        Question("monthly_budget_headroom", "enum", choices=("none", "low", "medium", "high")),
        Question("travel_next_30_days_trips", "range", low=0, high=10),
    )
}


def ask(question_id: Any) -> Question:
    """The registered question for an id; anything else is refused. The cloud sends only the id."""
    if not isinstance(question_id, str) or question_id not in REGISTRY:
        raise LobeError("unknown_question")
    return REGISTRY[question_id]


def check(question: Question, answer: Any) -> bool | str | int:
    """The answer, if it is exactly the typed shape the question allows; otherwise `LobeError`."""
    if question.kind == "bool":
        if type(answer) is not bool:  # not 0/1, not "true", not None
            raise LobeError("not_a_boolean")
        return answer
    if question.kind == "enum":
        if type(answer) is not str or answer not in question.choices:  # exact: no case folding, no trimming
            raise LobeError("not_a_listed_value")
        return answer
    if type(answer) is not int:  # not a float, a bool, a numeric string
        raise LobeError("not_a_whole_number")
    if not question.low <= answer <= question.high:
        raise LobeError("outside_the_range")
    return answer


def receive(envelope: Any) -> tuple[str, bool | str | int]:
    """Validate what the device sends back: exactly {"question": <id>, "answer": <typed value>}."""
    if type(envelope) is not dict or set(envelope) != {"question", "answer"}:
        raise LobeError("wrong_shape")
    question = ask(envelope["question"])
    return question.id, check(question, envelope["answer"])


def free_text_fields(value: Any) -> int:
    """How many strings in a payload are not a registered enum choice: the experiment's boundary metric, target 0.
    Counts every string leaf and every dict key, at any depth, that no registered question lists as a choice."""
    allowed = {c for q in REGISTRY.values() for c in q.choices}
    n = 0
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, item in v.items():
                n += isinstance(k, str) and k not in {"question", "answer"} and k not in REGISTRY
                stack.append(item)
        elif isinstance(v, list | tuple):
            stack.extend(v)
        elif isinstance(v, str):
            n += v not in allowed and v not in REGISTRY
        elif isinstance(v, float) and not math.isfinite(v):
            n += 1
    return n
