"""Keeping a model's reasoning out of what people read.

Some models (Qwen3, DeepSeek-R1 and friends, run locally or through a gateway) put their reasoning
in the answer text inside `<think>...</think>`. Reasoning is for the owner's live "thinking" view
only; it must never be part of a reply on any channel. `strip_think` cleans finished text and
`ThinkFilter` does the same for a stream, holding back only what could still turn into a tag.
"""

from __future__ import annotations

import re

OPEN, CLOSE = "<think>", "</think>"
_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_UNCLOSED = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
_STRAY_CLOSE = re.compile(r"\A.*?</think>\s*", re.DOTALL | re.IGNORECASE)  # the opening tag was in the template


def strip_think(text: str) -> str:
    """The text without `<think>` blocks. An unclosed block (cut off by the token limit) is dropped to
    its end; a lone `</think>` means everything before it was reasoning."""
    if "think>" not in text.lower():
        return text
    out = _BLOCK.sub("", text)
    out = _UNCLOSED.sub("", out)
    if CLOSE in out.lower():
        out = _STRAY_CLOSE.sub("", out)
    return out


class ThinkFilter:
    """Streamed text without `<think>` blocks."""

    def __init__(self) -> None:
        self.raw = ""  # everything fed so far
        self.sent = 0  # how much of strip_think(raw) was already given out

    def feed(self, delta: str) -> str:
        self.raw += delta
        clean = strip_think(self.raw)
        # Hold back a tail that could be the start of a tag (or of its closing part).
        hold = 0
        for tag in (OPEN, CLOSE):
            for n in range(min(len(tag) - 1, len(clean)), 0, -1):
                if clean.lower().endswith(tag[:n]):
                    hold = max(hold, n)
                    break
        safe = clean[: len(clean) - hold]
        if len(safe) < self.sent:  # a later tag rewrote text already shown: nothing can be taken back
            return ""
        out, self.sent = safe[self.sent :], len(safe)
        return out

    def flush(self) -> str:
        clean = strip_think(self.raw)
        out, self.sent = clean[self.sent :], len(clean)
        return out
