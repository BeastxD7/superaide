"""Citations for models without native ones (ADR 0007, section 5).

Sources go to the model as labelled text blocks; the model cites them with `[<id>]` right after
the claim. This module turns those markers into neutral `Citation`s, keeping only ids that a
tool actually returned in this conversation. Anything else is stripped: a source Ava didn't
look up is never shown as one.
"""

from __future__ import annotations

import logging
import re

from superaide.brain.providers.types import Citation, SourceDoc, Text

log = logging.getLogger(__name__)

RULE = (
    "Sources from your tools arrive as [source id=… title=…] blocks. When a sentence uses one, put its id in "
    "square brackets right after that sentence, e.g. “Your flight is on 3 Nov [ava://memory/12].” Cite only ids "
    "you were given; never invent one and never cite a source you didn't use."
)

_ID = r"(?:ava://[^\s\],]+|https?://[^\s\],]+)"
MARKER = re.compile(rf"\s*\[\s*({_ID}(?:\s*,\s*{_ID})*)\s*\]")


def render_sources(sources: list[SourceDoc]) -> str:
    """Tool results as text a model without native citations can quote and cite."""
    blocks = []
    for s in sources:
        title = s.title.replace('"', "'")
        blocks.append(f'[source id={s.id} title="{title}"]\n{s.text}\n[/source]')
    return "\n\n".join(blocks)


def extract(text: str, sources: list[SourceDoc]) -> tuple[list[Text], list[str]]:
    """Split a reply at its citation markers. Returns text parts (each carrying the citations
    that close it) and the ids that were dropped because no tool returned them."""
    known = {s.id: s for s in sources}
    parts: list[Text] = []
    dropped: list[str] = []
    pending = ""
    pos = 0
    for m in MARKER.finditer(text):
        pending += text[pos : m.start()]
        pos = m.end()
        cites = []
        for raw in re.split(r"\s*,\s*", m.group(1)):
            src = known.get(raw.strip())
            if src is None:
                dropped.append(raw.strip())
            else:
                cites.append(Citation(src.id, src.title, "", src.kind, verified=True))
        if not cites:
            continue
        if pending or not parts:
            parts.append(Text(pending, cites))
            pending = ""
        else:  # markers right after markers: add to the same claim
            parts[-1].citations.extend(c for c in cites if c not in parts[-1].citations)
    pending += text[pos:]
    if pending:
        parts.append(Text(pending))
    if dropped:
        log.info("dropped %d citation(s) to sources no tool returned", len(dropped))
    return parts or [Text("")], dropped


_PREFIXES = ("ava://", "https://", "http://")


def _could_be_marker(tail: str) -> bool:
    """Whether `tail` (starting at "[") could still grow into a citation marker."""
    body = tail[1:].lstrip()
    if len(tail) > 400 or "]" in tail:
        return False
    head = body.split(",")[-1].strip() if body else ""
    if not body:
        return True
    first = body.split(",")[0].strip()
    ok_first = any(first.startswith(p) or p.startswith(first) for p in _PREFIXES)
    ok_last = not head or any(head.startswith(p) or p.startswith(head) for p in _PREFIXES)
    return ok_first and ok_last and not any(c.isspace() for c in body.strip().replace(", ", ","))


class MarkerFilter:
    """Takes streamed text and gives it back without citation markers, holding back only a
    possible partial marker at the end of a chunk. The final text is cited properly by `extract`."""

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, delta: str) -> str:
        text = self.pending + delta
        self.pending = ""
        start = text.rfind("[")
        if start != -1 and _could_be_marker(text[start:]):
            # Keep the whitespace before it too: a marker takes its leading space with it.
            cut = len(text[:start].rstrip())
            text, self.pending = text[:cut], text[cut:]
        else:
            # Trailing spaces wait for the next chunk, in case a marker follows and takes them.
            cut = len(text.rstrip())
            text, self.pending = text[:cut], text[cut:]
        return MARKER.sub("", text)

    def flush(self) -> str:
        text, self.pending = self.pending, ""
        return MARKER.sub("", text)
