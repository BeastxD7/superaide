"""Server-Sent Events for a chat turn: the turn runs in its own thread and its progress
events are forwarded as they happen, then `done` with the reply.

The turn is never tied to the connection: if the browser goes away, it still finishes and
the reply is saved, so reloading the chat shows it.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from superaide.brain.llm import OnEvent
from superaide.pipeline import HandleResult

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream_turn(run: Callable[[OnEvent], HandleResult]) -> Iterator[str]:
    events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put((kind, data))

    def work() -> None:
        try:
            result = run(emit)
            events.put(("done", {"mode": result.mode, "reply": result.reply, "notes": result.notes}))
        except Exception:
            log.exception("streamed chat turn failed")
            events.put(("error", {"message": "Something went wrong on my side. Please try again."}))
        finally:
            events.put(None)

    threading.Thread(target=work, name="chat-turn", daemon=True).start()
    yield ": open\n\n"
    while True:
        try:
            item = events.get(timeout=KEEPALIVE_SECONDS)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if item is None:
            return
        yield sse(*item)
