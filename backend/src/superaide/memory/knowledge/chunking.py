"""Splitting a document into passages (issue #47), so a long file or website is searchable end to end.

Passages follow paragraph boundaries and stay under CHUNK_CHARS, so a citation points at a few lines the
owner can read, not a whole page. No overlap: a passage is shown as it is.
"""

from __future__ import annotations

import re

CHUNK_CHARS = 1500
MAX_CHUNKS = 400  # a 60,000-character document is about 40


def split(text: str, size: int = CHUNK_CHARS) -> list[str]:
    out: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:  # one giant paragraph: cut at a sentence end, else at a space
            cut = max(para.rfind(". ", 0, size), para.rfind("\n", 0, size), para.rfind(" ", 0, size))
            cut = cut + 1 if cut > size // 3 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return [c for c in out if c][:MAX_CHUNKS]


def windows(chunks: list[tuple[object, str]], max_chars: int, max_windows: int) -> list[list[tuple[object, str]]]:
    """Consecutive (id, text) chunks grouped so each group's text fits one model call."""
    groups: list[list[tuple[object, str]]] = []
    cur: list[tuple[object, str]] = []
    size = 0
    for item in chunks:
        if cur and size + len(item[1]) > max_chars:
            groups.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += len(item[1]) + 2
    if cur:
        groups.append(cur)
    return groups[:max_windows]


_WORD = re.compile(r"[a-z0-9]{3,}")


def best_chunk(fact: str, chunks: list[tuple[object, str]]) -> object | None:
    """The chunk a fact most likely came from: the one sharing the most words with it (deterministic)."""
    want = set(_WORD.findall(fact.lower()))
    best, score = None, 0
    for cid, text in chunks:
        n = len(want & set(_WORD.findall(text.lower())))
        if n > score:
            best, score = cid, n
    return best
