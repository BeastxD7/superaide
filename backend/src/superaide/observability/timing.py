"""Per-stage timing of one message turn (#412): numbers only, never text.

A `TurnTimer` is opened around a turn (`pipeline.Agent.handle`) and kept in a context variable, so a stage deep in
the code (the model call, the Airlock, the policy check) is timed with `with span("model"):` and no plumbing. With
no timer open every helper here is a no-op, so jobs, tests and tools that never open one are unaffected.

Times are exclusive: when a span opens inside another (the Airlock inside the model call, the send inside the
reply) the outer one is paused, so the stages add up to the turn instead of counting the same milliseconds twice.
The clock is injectable so tests can drive it by hand.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("superaide.timing")

# The stages of the pipeline, in the order a turn meets them. Each is one integer column of `turn_timing`.
CLOCK: Callable[[], float] = time.perf_counter  # tests replace this with a hand-driven clock
STAGES = ("verify", "identity", "gate", "retrieval", "airlock", "model", "policy", "reply", "send")


class TurnTimer:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock = clock or CLOCK
        self._start = clock()
        self._seconds: dict[str, float] = {}
        self._stack: list[list[Any]] = []  # [name, time this piece started]
        self.model_calls = 0
        self.first_token_s: float | None = None

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        now = self._clock()
        if self._stack:  # pause the outer stage: its time so far is its own
            outer = self._stack[-1]
            self._seconds[outer[0]] = self._seconds.get(outer[0], 0.0) + now - outer[1]
        entry: list[Any] = [name, now]
        self._stack.append(entry)
        if name == "model":
            self.model_calls += 1
        try:
            yield
        finally:
            now = self._clock()
            self._stack.pop()
            self._seconds[name] = self._seconds.get(name, 0.0) + now - entry[1]
            if self._stack:
                self._stack[-1][1] = now

    def first_token(self) -> None:
        if self.first_token_s is None:
            self.first_token_s = self._clock() - self._start

    def total_ms(self) -> int:
        return round((self._clock() - self._start) * 1000)

    def stage_ms(self) -> dict[str, int]:
        """Only the stages that ran: a stage a turn never reached is missing, not 0, so it cannot drag a median down."""
        return {name: round(sec * 1000) for name, sec in self._seconds.items()}

    def first_token_ms(self) -> int | None:
        return None if self.first_token_s is None else round(self.first_token_s * 1000)


_current: ContextVar[TurnTimer | None] = ContextVar("turn_timer", default=None)


def current() -> TurnTimer | None:
    return _current.get()


@contextmanager
def open_turn(clock: Callable[[], float] | None = None) -> Iterator[TurnTimer]:
    timer = TurnTimer(clock)
    token = _current.set(timer)
    try:
        yield timer
    finally:
        _current.reset(token)


def span(name: str) -> AbstractContextManager[None]:
    timer = _current.get()
    return nullcontext() if timer is None else timer.span(name)


def first_token() -> None:
    if (timer := _current.get()) is not None:
        timer.first_token()


def timed[**P, R](name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator form of `span`, for a function every caller should have timed (the policy check)."""

    def wrap(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(name):
                return fn(*args, **kwargs)

        return inner

    return wrap


class TimedLLM:
    """Times each model call of a turn as the `model` stage. Everything else is the wrapped model's own."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    def step(self, **kwargs: Any) -> Any:
        with span("model"):
            return self._llm.step(**kwargs)

    def complete(self, **kwargs: Any) -> Any:
        with span("model"):
            return self._llm.complete(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


def timed_llm(llm: Any) -> Any:
    return llm if _current.get() is None or isinstance(llm, TimedLLM) else TimedLLM(llm)


def log_line(timer: TurnTimer, *, provider: str, mode: str) -> str:
    """One structured line per turn, all numbers (and the channel and outcome words): `key=value` pairs."""
    parts = [f"provider={provider}", f"mode={mode}", f"total_ms={timer.total_ms()}"]
    parts += [f"{name}_ms={ms}" for name, ms in timer.stage_ms().items()]
    parts.append(f"model_calls={timer.model_calls}")
    if (ttft := timer.first_token_ms()) is not None:
        parts.append(f"first_token_ms={ttft}")
    return "turn_timing " + " ".join(parts)
