"""Effect-stub replay (issue #110).

In replay mode the executor, not the skill, decides what runs: reads are answered from a snapshot (the
result recorded for the same call) or, failing that, by the real read; every other effect (`write_own`,
`write_external`, `send`, `spend`, and any tool we do not know) returns a canned result and does
nothing. The calls made are kept, so two runs can be compared by name and argument hash alone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from superaide.tooltrace.effects import effect_of, is_read
from superaide.tooltrace.recorder import Call, digest

CANNED = "(replay) Not carried out: this is a rehearsal, nothing was changed or sent."


@dataclass
class Replayer:
    """Hand this to the loop; it is asked before every tool call."""

    snapshot: dict[tuple[str, str], Any] = field(default_factory=dict)  # (tool, args_hash) -> recorded result
    calls: list[Call] = field(default_factory=list)
    stubbed: int = 0

    def handle(self, name: str, args: dict[str, Any], real: Callable[[], tuple[Any, bool]]) -> tuple[Any, bool]:
        h = digest(args)
        self.calls.append(Call(name, effect_of(name), h))
        if not is_read(name):
            self.stubbed += 1
            return CANNED, False
        if (name, h) in self.snapshot:
            return self.snapshot[(name, h)], False
        return real()


@dataclass(frozen=True)
class Diff:
    same: bool
    only_before: list[Call]
    only_after: list[Call]

    def summary(self) -> str:
        if self.same:
            return "No difference."
        n = len(self.only_before) + len(self.only_after)
        return f"{n} thing{'s' if n != 1 else ''} would be done differently."


def diff(before: Sequence[Call], after: Sequence[Call]) -> Diff:
    """Order-sensitive comparison of two runs of the same task, by tool name and argument hash."""
    if list(before) == list(after):
        return Diff(True, [], [])
    left, right = list(before), list(after)
    only_before = [c for c in left if c not in right]
    only_after = [c for c in right if c not in left]
    if not only_before and not only_after:  # same calls, different order or count
        only_before, only_after = left, right
    return Diff(False, only_before, only_after)


def drill(runs: Sequence[tuple[Sequence[Call], Sequence[Call]]]) -> str:
    """ "5 of 200 would change", for a model swap or a new skill version."""
    changed = sum(1 for before, after in runs if not diff(before, after).same)
    return f"{changed} of {len(runs)} would change."
