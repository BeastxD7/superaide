"""Find what Ava knows about a question: notes and past messages, behind the audience gate.

Three searches run and their rankings are fused (reciprocal rank fusion, k = 60):
- every word of the question (AND), each word widened to its en-GB/en-US spellings and, for
  preference words, synonyms (see `lexicon`), so "what colors do I hate" finds "does not like
  the colour black";
- any word of it (OR), for questions the model padded with extra words;
- trigram similarity (pg_trgm), for typos ("clour"), when the extension is installed.
A channel ("telegram", "slack", "web") narrows the search to what was said there.

Every result carries an `ava://` id so a reply can cite it and the owner can click through.
The gate lives in every search's SQL: a note is visible under `memory_filter`, and a message
only if everyone in the current audience was in that message's audience.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, case, desc, func, literal, select, text
from sqlalchemy.orm import Session, aliased

from superaide.context import lexicon
from superaide.context.audience import Audience, memory_filter
from superaide.db.models import Context, Event, MemoryItem
from superaide.memory.echo import label as echo_label
from superaide.memory import importance

LANG = "english"
RRF_K = 60
TRGM_MIN = 0.4  # word_similarity: "colour" in "… clour …" 0.44, "black" in "… back …" 0.38
CHANNELS = {"telegram": "telegram", "slack": "slack", "web": "web", "app": "web"}
CHANNEL_NAMES = {"telegram": "Telegram", "slack": "Slack", "web": "the app"}
_trgm_ready: dict[str, bool] = {}


@dataclass
class Source:
    uri: str  # ava://memory/<id> | ava://msg/<provider>/<event id>
    kind: str  # memory | message
    title: str
    text: str
    at: datetime


def channel_name(provider: str | None) -> str:
    return CHANNEL_NAMES.get(provider or "", provider or "")


# --- searches --------------------------------------------------------------------


def _tsquery(term_groups: list[set[str]], *, any_word: bool) -> Any | None:
    """`(colour | color) & black`, or with any_word `colour | color | black`."""
    groups = [sorted(g) for g in term_groups if g]
    if not groups:
        return None
    if any_word:
        expr = " | ".join(w for g in groups for w in g)
    else:
        expr = " & ".join("(" + " | ".join(g) + ")" for g in groups)
    return func.to_tsquery(LANG, expr)  # words are letters/marks/digits only (lexicon._tokens), so this is safe


def _has_trgm(session: Session) -> bool:
    url = str(session.get_bind().engine.url)
    if url not in _trgm_ready:
        _trgm_ready[url] = bool(session.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")))
    return _trgm_ready[url]


def _trgm_score(column: Any, words: list[str]) -> Any | None:
    long = [w for w in words if len(w) >= 5][:6]
    if not long:
        return None
    scores = [func.word_similarity(w, column) for w in long]
    return scores[0] if len(scores) == 1 else func.greatest(*scores)


def _arms(
    session: Session, query: str, column: Any, base: Callable[[], Select[Any]], limit: int, strict: bool
) -> list[list[Any]]:
    """The ranked rows of each search over `column`, each built on the gated `base()` query."""
    term_groups = lexicon.groups(query)
    doc = func.to_tsvector(LANG, column)
    arms: list[list[Any]] = []
    for any_word in (False,) if strict else (False, True):
        q = _tsquery(term_groups, any_word=any_word)
        if q is None:
            continue
        stmt = base().where(doc.op("@@")(q)).order_by(desc(func.ts_rank_cd(doc, q))).limit(limit)
        arms.append(list(session.scalars(stmt)))
    if not strict and _has_trgm(session):
        score = _trgm_score(column, lexicon.words(query))
        if score is not None:
            arms.append(list(session.scalars(base().where(score >= TRGM_MIN).order_by(desc(score)).limit(limit))))
    return arms


def _fuse(arms: list[list[Any]]) -> list[Any]:
    """Reciprocal rank fusion, times each note's importance and decay (memory/importance.py); ties go to
    the newer row."""
    score: dict[Any, float] = {}
    rows: dict[Any, Any] = {}
    for arm in arms:
        for rank, row in enumerate(arm):
            score[row.id] = score.get(row.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            rows[row.id] = row
    return sorted(rows.values(), key=lambda r: (score[r.id] * importance.weight(r), r.created_at), reverse=True)


def _contexts(session: Session, ids: set[Any]) -> dict[Any, Context]:
    if not ids:
        return {}
    return {c.id: c for c in session.scalars(select(Context).where(Context.id.in_(ids)))}


# --- notes and messages ------------------------------------------------------------


def recall_memories(
    session: Session,
    aud: Audience,
    query: str,
    limit: int = 6,
    channel: str | None = None,
    strict: bool = False,
) -> list[Source]:
    origin = aliased(Context)

    def base() -> Select[Any]:
        stmt = select(MemoryItem).where(memory_filter(aud))
        if channel:
            stmt = stmt.join(origin, origin.id == MemoryItem.origin_context_id).where(origin.provider == channel)
        return stmt

    arms = _arms(session, query, MemoryItem.content, base, limit * 2, strict)
    if not strict:
        from superaide.context.vectors import meaning_arm

        arms.append(meaning_arm(session, base, query, limit * 2))
    return _memory_sources(session, _fuse(arms)[:limit], channel)


def _memory_sources(session: Session, found: list[MemoryItem], channel: str | None = None) -> list[Source]:
    contexts = _contexts(session, {m.origin_context_id for m in found if m.origin_context_id})
    out = []
    for m in found:
        where = contexts.get(m.origin_context_id) if m.origin_context_id else None
        if m.source in CHANNEL_NAMES:  # said on a channel: "from Telegram", not "from telegram"
            src = f" · from {channel_name(m.source)}"
        elif m.source == "chat" and where is not None:
            src = f" · from {channel_name(where.provider)}"
        else:
            src = f" · from {m.source}" if m.source else ""
        out.append(
            Source(uri=f"ava://memory/{m.id}", kind="memory", title=f"Note · {m.category}{src}{echo_label(m)}",
                   text=m.content, at=m.created_at)
        )  # fmt: skip
    return out


def _message_gate(aud: Audience) -> ColumnElement[bool]:
    return Event.audience_person_ids.contains(sorted(aud.person_ids))


def recall_messages(
    session: Session, aud: Audience, query: str, limit: int = 6, channel: str | None = None
) -> list[Source]:
    if aud.has_unknown or not aud.person_ids:
        return []  # only for a known audience, and only what they all saw

    def base() -> Select[Any]:
        # Only what people said. Ava's own past replies are never evidence: an outdated hedge
        # ("I may not keep this…") cited back to her becomes a "fact". They reach her only as the
        # conversation's history.
        stmt = select(Event).where(_message_gate(aud), Event.content != "", Event.direction == "in")
        if channel:
            stmt = stmt.join(Context, Context.id == Event.context_id).where(Context.provider == channel)
        return stmt

    if lexicon.words(query):
        found = _fuse(_arms(session, query, Event.content, base, limit * 2, strict=False))[:limit]
    elif channel:  # "what did I say on Telegram?": the latest there
        found = list(session.scalars(base().order_by(Event.created_at.desc()).limit(limit)))
    else:
        found = []
    contexts = _contexts(session, {e.context_id for e in found})
    out = []
    for e in found:
        provider = contexts[e.context_id].provider if e.context_id in contexts else ""
        who = "You" if e.direction == "in" else "Ava"
        out.append(
            Source(
                uri=f"ava://msg/{provider}/{e.id}",
                kind="message",
                title=f"{who} · {provider} · {e.created_at:%d %b %Y}",
                text=e.content[:1000],
                at=e.created_at,
            )
        )
    return out


def _channel_in(query: str) -> tuple[str | None, str]:
    """A channel named in the query ("on Telegram") becomes a filter, not a search word."""
    found: str | None = None
    kept = []
    for w in query.split():
        key = lexicon.words(w)
        if found is None and key and key[0] in {"telegram", "slack"}:
            found = CHANNELS[key[0]]
            continue
        kept.append(w)
    return found, " ".join(kept)


def recall(
    session: Session,
    aud: Audience,
    query: str,
    limit: int = 8,
    exclude_event: uuid.UUID | None = None,
    channel: str | None = None,
) -> list[Source]:
    """Notes and messages fused into one list; never more than `limit`."""
    query = " ".join(query.split())[:300]
    picked = CHANNELS.get((channel or "").strip().lower())
    if picked is None:
        picked, query = _channel_in(query)
    if not lexicon.words(query) and not picked:
        return []
    notes = recall_memories(session, aud, query, limit=limit, channel=picked) if lexicon.words(query) else []
    msgs = [
        m
        for m in recall_messages(session, aud, query, limit=limit, channel=picked)
        if not m.uri.endswith(str(exclude_event))
    ]
    # Notes are distilled, so they lead; messages always keep a couple of seats when they match.
    seats = max(limit - min(len(msgs), 2), limit // 2)
    return (notes[:seats] + msgs + notes[seats:])[:limit]


FUZZY_WORD_MIN = 0.45  # "favrite" in "… favourite …" 0.5, "cusine" in "… cuisine …" 0.5
FUZZY_ONE_WORD_MIN = 0.6  # with a single long word, it alone must be close


def fuzzy_memories(session: Session, aud: Audience, query: str, limit: int = 4) -> list[Source]:
    """Notes that two or more of the question's longer words are close to, each on its own, for
    questions with typos in several words ("whats my favrite cusine"). Stricter than the trigram
    arm of `recall` (which needs one close word), because it runs before the model, unasked."""
    if not _has_trgm(session):
        return []
    long = list(dict.fromkeys(w for w in lexicon.words(query) if len(w) >= 5))[:6]
    if not long:
        return []
    sims = [func.word_similarity(w, MemoryItem.content) for w in long]
    need, floor = (1, FUZZY_ONE_WORD_MIN) if len(long) == 1 else (2, FUZZY_WORD_MIN)
    close = sum((case((sim >= floor, 1), else_=0) for sim in sims), start=literal(0))
    total = sum(sims, start=literal(0.0))
    stmt = select(MemoryItem).where(memory_filter(aud), close >= need).order_by(desc(total)).limit(limit)
    return _memory_sources(session, list(session.scalars(stmt)))


def pre_recall(session: Session, aud: Audience, query: str, limit: int = 4) -> list[Source]:
    """Notes matching every word of the owner's message (spellings and synonyms included),
    looked up by code before the model runs, so a fact never depends on the model's query.
    When nothing matches exactly, notes close to two or more of its words (typos)."""
    if not lexicon.words(query):
        return []
    query = " ".join(query.split())[:300]
    return recall_memories(session, aud, query, limit=limit, strict=True) or fuzzy_memories(session, aud, query, limit)
