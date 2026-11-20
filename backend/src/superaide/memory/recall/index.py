"""The derived recall index (#647, report 5.2 and 7.3): one table, `recall_doc`, built from the source tables, and one
statement that searches it.

The sources (`memory_item`, `event`, `reception_message`, `document_chunk`, `episode`) stay the truth. `sync` rebuilds
this table from them, idempotently: a row is inserted, changed only when something about it changed (and then its
vectors are dropped so they are re-embedded), or retired when its source stopped qualifying. Nothing here is written by
the model.

`search` is one SQL statement: the audience gate, the instance scope, validity and the source-is-still-live check
come first in every arm; then a full-text arm (all words, any word), a trigram arm and a meaning arm; then reciprocal
rank fusion. A row whose source was erased, retired, or whose note's audience changed since the last `sync` is
hidden by the statement itself, so a lagging index fails closed instead of leaking.

Taint (`owner | known | stranger | document | web`) is a column, so the loop can mark a turn tainted from what was
returned, not from a title prefix. This module is not wired into the live `recall` yet: it is compared with it in
shadow (tests/memory/recall/test_recall_index.py) before anything switches.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Float,
    Select,
    String,
    and_,
    case,
    cast,
    delete,
    desc,
    exists,
    false,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
    true,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID, array, insert
from sqlalchemy.orm import Session

from superaide.memory.audience import CLOCK_SKEW, Audience
from superaide.memory.models import Context, Document, DocumentChunk, Episode, Event, MemoryItem, RecallDoc
from superaide.memory.recall import lexicon
from superaide.memory.recall.retrieval import RRF_K, TRGM_MIN, VISITOR_TRGM_MIN, _has_trgm, _tsquery
from superaide.memory.recall.vectors import (
    RECALL_TABLE,
    Embedder,
    SizedVector,
    Vector,
    get_embedder,
    recall_embedding,
)
from superaide.platform.config import get_settings
from superaide.platform.tenancy.context import scope_or_default
from superaide.platform.tenancy.service import instance_owner
from superaide.reception.models import ReceptionMessage

log = logging.getLogger(__name__)

KINDS = ("note", "message", "visitor_message", "chunk", "episode")  # `approval` is a later slice
MESSAGE_KINDS = ("message", "visitor_message")  # other people's words: a stricter typo threshold, like today
TAINTS = ("owner", "known", "stranger", "document", "web")
# A turn that read one of these must not relay (brain/loop.py today decides this from a title prefix).
TAINTING = ("stranger", "document", "web")
BATCH = 64
_UUID_ARRAY = ARRAY(UUID(as_uuid=True))


# --- the gate -------------------------------------------------------------------------------


def _const(value: bool) -> ColumnElement[bool]:
    return true() if value else false()


def doc_filter(aud: Audience, *, rooms: bool = False, now: datetime | None = None) -> ColumnElement[bool]:
    """The audience gate over `recall_doc`: the same rule as `memory_filter` for notes, `_message_gate` for messages,
    `_owner_gate` for visitor messages and `chunk_filter` for passages, written once over the gate columns. `rooms`:
    also the messages stored from channels the owner let Ava read whole (only the owner's own private chat passes it).
    Unknown people present, or no known person: only public rows."""
    now = now or datetime.now(UTC)
    d = RecallDoc
    live = and_(
        d.retired_at.is_(None),
        or_(d.valid_to.is_(None), d.valid_to > now),
        d.valid_from <= now + CLOCK_SKEW,
        d.agent_id == scope_or_default().agent_id,
    )
    if aud.has_unknown:
        return and_(live, d.allowed_kind == "public", d.has_unknown.is_(False))
    known = bool(aud.person_ids)
    allowed = or_(
        d.allowed_kind == "public",
        and_(d.allowed_kind == "org", _const(aud.all_internal)),
        and_(
            d.allowed_kind == "persons",
            _const(known),
            d.has_unknown.is_(False),
            d.allowed_person_ids.contains(sorted(aud.person_ids)),  # allowed ⊇ audience
        ),
        and_(d.allowed_kind == "room", _const(rooms and known)),
    )
    return and_(live, allowed, d.min_trust <= aud.min_trust)


def source_live() -> ColumnElement[bool]:
    """The row's source still qualifies *now*, checked against the source table in the same statement: not erased,
    not retired, not in conflict, a document still shared; and for a note, its audience unchanged since indexing."""
    d = RecallDoc
    m, e, v, c, doc, ep = MemoryItem, Event, ReceptionMessage, DocumentChunk, Document, Episode
    now = datetime.now(UTC)
    note = exists().where(
        m.id == d.ref_id,
        m.erased_at.is_(None),
        m.conflict_status == "none",
        or_(m.valid_to.is_(None), m.valid_to > now),
        m.allowed_kind == d.allowed_kind,
        m.allowed_person_ids == d.allowed_person_ids,
        m.min_trust == d.min_trust,
    )
    message = exists().where(e.id == d.ref_id, e.erased_at.is_(None))
    visitor = exists().where(v.id == d.ref_id, v.status != "spam")
    chunk = exists().where(c.id == d.ref_id, doc.id == c.document_id, doc.removed_at.is_(None), doc.erased_at.is_(None))
    episode = exists().where(ep.id == d.ref_id)
    return case(
        (d.kind == "note", note),
        (d.kind == "message", message),
        (d.kind == "visitor_message", visitor),
        (d.kind == "chunk", chunk),
        (d.kind == "episode", episode),
        else_=false(),
    )


# --- building the index from the sources ---------------------------------------------------------


def _row(kind: str, ref: Any, **cols: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "said_by_person_id": literal(None, UUID(as_uuid=True)),
        "said_by_identity_id": literal(None, UUID(as_uuid=True)),
        "provider": literal(""),
        "context_id": literal(None, UUID(as_uuid=True)),
        "thread_id": literal(None, String),
        "valid_to": literal(None, RecallDoc.valid_to.type),
        "has_unknown": false(),
        "min_trust": literal(0),
        "taint": literal("known"),
    }
    base.update(kind=literal(kind), ref_id=ref, retired_at=literal(None, RecallDoc.retired_at.type), **cols)
    return base


def _sources(session: Session) -> dict[str, tuple[dict[str, Any], Select[Any]]]:
    """Per kind: the columns of a `recall_doc` row as expressions over the source, and the select that yields the
    sources that qualify. One definition feeds both the upsert and the retire pass."""
    agent = scope_or_default().agent_id
    found = instance_owner(session)
    owner_id = found[1].id if found else None
    out: dict[str, tuple[dict[str, Any], Select[Any]]] = {}

    note_taint = case((MemoryItem.document_id.is_not(None), "document"), else_="owner")
    out["note"] = (
        _row(
            "note",
            MemoryItem.id,
            provider=func.coalesce(Context.provider, ""),
            context_id=MemoryItem.origin_context_id,
            occurred_at=MemoryItem.created_at,
            valid_from=MemoryItem.valid_from,
            valid_to=MemoryItem.valid_to,
            allowed_kind=MemoryItem.allowed_kind,
            allowed_person_ids=MemoryItem.allowed_person_ids,
            min_trust=MemoryItem.min_trust,
            taint=note_taint,
            text=MemoryItem.content,
        ),  # fmt: skip
        select(MemoryItem.id)
        .outerjoin(Context, Context.id == MemoryItem.origin_context_id)
        .where(MemoryItem.agent_id == agent, MemoryItem.erased_at.is_(None), MemoryItem.conflict_status == "none"),
    )

    ambient = Event.meta.has_key("ambient")
    who = case((Event.resolved_person_id == owner_id, "owner"), else_="known") if owner_id else literal("known")
    out["message"] = (
        _row(
            "message",
            Event.id,
            said_by_person_id=Event.resolved_person_id,
            said_by_identity_id=Event.sender_identity_id,
            provider=func.coalesce(Context.provider, ""),
            context_id=Event.context_id,
            thread_id=Event.thread_id,
            occurred_at=Event.created_at,
            valid_from=Event.created_at,
            allowed_kind=case((ambient, "room"), else_="persons"),
            allowed_person_ids=case((ambient, literal_column("'{}'::uuid[]")), else_=Event.audience_person_ids),
            has_unknown=Event.audience_has_unknown,
            taint=case((ambient, "stranger"), else_=who),
            text=Event.content,
        ),  # fmt: skip
        select(Event.id)
        .outerjoin(Context, Context.id == Event.context_id)
        .where(Event.agent_id == agent, Event.direction == "in", Event.content != "", Event.erased_at.is_(None)),
    )

    if owner_id is not None:
        owner_only = cast(array([owner_id]), _UUID_ARRAY)
        out["visitor_message"] = (
            _row(
                "visitor_message",
                ReceptionMessage.id,
                said_by_identity_id=ReceptionMessage.identity_id,
                provider=ReceptionMessage.provider,
                context_id=ReceptionMessage.context_id,
                occurred_at=ReceptionMessage.created_at,
                valid_from=ReceptionMessage.created_at,
                allowed_kind=literal("persons"),
                allowed_person_ids=owner_only,
                taint=literal("stranger"),
                text=ReceptionMessage.message,
            ),  # fmt: skip
            select(ReceptionMessage.id).where(
                ReceptionMessage.agent_id == agent, ReceptionMessage.status != "spam", ReceptionMessage.message != ""
            ),
        )

    out["chunk"] = (
        _row(
            "chunk",
            DocumentChunk.id,
            said_by_person_id=Document.owner_person_id,
            occurred_at=DocumentChunk.created_at,
            valid_from=DocumentChunk.created_at,
            allowed_kind=literal("persons"),
            allowed_person_ids=cast(array([Document.owner_person_id]), _UUID_ARRAY),
            min_trust=literal(2),
            taint=literal("document"),
            text=DocumentChunk.text,
        ),  # fmt: skip
        select(DocumentChunk.id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.agent_id == agent, Document.removed_at.is_(None), Document.erased_at.is_(None)),
    )

    out["episode"] = (
        _row(
            "episode",
            Episode.id,
            said_by_person_id=Episode.owner_person_id,
            provider=Episode.channel,
            context_id=Episode.context_id,
            occurred_at=Episode.ended_at,
            valid_from=Episode.started_at,
            allowed_kind=literal("persons"),
            allowed_person_ids=Episode.audience_person_ids,
            has_unknown=Episode.audience_has_unknown,
            min_trust=literal(2),
            text=Episode.summary + " " + func.array_to_string(Episode.hints, " "),
        ),  # fmt: skip
        select(Episode.id).where(Episode.agent_id == agent, Episode.summary != ""),
    )
    return out


_COLUMNS = (
    "kind", "ref_id", "said_by_person_id", "said_by_identity_id", "provider", "context_id", "thread_id",
    "occurred_at", "valid_from", "valid_to", "retired_at", "allowed_kind", "allowed_person_ids", "has_unknown",
    "min_trust", "taint", "text",
)  # fmt: skip


def _has_recall_embedding(session: Session) -> bool:
    return session.scalar(text(f"SELECT to_regclass('{RECALL_TABLE}') IS NOT NULL")) is True


def sync(session: Session, kinds: tuple[str, ...] = KINDS) -> dict[str, int]:
    """Rebuild this instance's index from the sources. Idempotent: running it twice changes nothing the second time.
    Returns, per kind, how many rows were added or changed (a changed row loses its vectors, to be re-embedded)."""
    agent = scope_or_default()
    changed: dict[str, int] = {}
    for kind, (cols, eligible) in _sources(session).items():
        if kind not in kinds:
            continue
        src = eligible.with_only_columns(*[cols[c] for c in _COLUMNS]).add_columns(
            func.gen_random_uuid().label("id"), literal(agent.tenant_id, UUID(as_uuid=True)).label("tenant_id"),
            literal(agent.agent_id, UUID(as_uuid=True)).label("agent_id"),
        )  # fmt: skip
        ins = insert(RecallDoc).from_select([*_COLUMNS, "id", "tenant_id", "agent_id"], src)
        distinct = or_(*[getattr(RecallDoc, c).is_distinct_from(getattr(ins.excluded, c)) for c in _COLUMNS[2:]])
        upsert = ins.on_conflict_do_update(
            constraint="uq_recall_doc_source", set_={c: getattr(ins.excluded, c) for c in _COLUMNS[2:]}, where=distinct
        ).returning(RecallDoc.id)
        ids = list(session.scalars(upsert))
        if ids and _has_recall_embedding(session):  # new rows have none; a changed row's vector is stale
            session.execute(delete(recall_embedding).where(recall_embedding.c.doc_id.in_(ids)))
        sources = cols["ref_id"]
        qualifying = eligible.with_only_columns(sources)
        retired: int = session.execute(
            update(RecallDoc)
            .where(
                RecallDoc.agent_id == agent.agent_id,
                RecallDoc.kind == kind,
                RecallDoc.retired_at.is_(None),
                RecallDoc.ref_id.not_in(qualifying),
            )
            .values(retired_at=datetime.now(UTC))
        ).rowcount  # type: ignore[attr-defined]
        changed[kind] = len(ids) + retired
    # kinds that cannot be built now (no owner yet): nothing qualifies, so whatever was indexed is retired
    for kind in set(kinds) - set(changed):
        session.execute(
            update(RecallDoc)
            .where(RecallDoc.agent_id == agent.agent_id, RecallDoc.kind == kind, RecallDoc.retired_at.is_(None))
            .values(retired_at=datetime.now(UTC))
        )
        changed[kind] = 0
    return changed


def retire(session: Session, kind: str, ref_ids: list[uuid.UUID]) -> int:
    """Retire these sources' rows now (soft: the row stays), for a caller that erases or retires a source and wants
    the index to follow in the same transaction. `search` already hides such a row; this keeps the table tidy."""
    if not ref_ids:
        return 0
    done: int = session.execute(
        update(RecallDoc)
        .where(RecallDoc.agent_id == scope_or_default().agent_id, RecallDoc.kind == kind, RecallDoc.ref_id.in_(ref_ids))
        .values(retired_at=datetime.now(UTC))
    ).rowcount  # type: ignore[attr-defined]
    return done


# --- vectors ---------------------------------------------------------------------------------------


def embed_missing(session: Session, emb: Embedder | None = None, max_rounds: int = 20) -> int:
    """Embed this instance's live index rows that have no vector for the current model. A new embedder starts a
    re-embed here, in the background; until it finishes the meaning arm finds nothing for that model and search stays
    keyword-only, never broken."""
    emb = emb or get_embedder(get_settings())
    if emb.name == "none" or not emb.ready() or not _has_recall_embedding(session):
        return 0
    re = recall_embedding.c
    done = 0
    for _ in range(max_rounds):
        rows = session.execute(
            select(RecallDoc.id, RecallDoc.tenant_id, RecallDoc.agent_id, RecallDoc.text)
            .where(
                RecallDoc.agent_id == scope_or_default().agent_id,
                RecallDoc.retired_at.is_(None),
                ~exists().where(re.doc_id == RecallDoc.id, re.model == emb.name),
            )
            .order_by(RecallDoc.occurred_at.desc())
            .limit(BATCH)
        ).all()
        if not rows:
            break
        session.commit()  # embedding is slow and must not hold locks (#526)
        vectors = emb.embed([r.text[:4000] for r in rows], kind="document")
        if not vectors:
            break
        now = datetime.now(UTC)
        stmt = insert(recall_embedding).values(
            [
                {"doc_id": r.id, "model": emb.name, "tenant_id": r.tenant_id, "agent_id": r.agent_id,
                 "embedding": v, "created_at": now}
                for r, v in zip(rows, vectors, strict=True)
            ]
        )  # fmt: skip
        session.execute(
            stmt.on_conflict_do_update(index_elements=["doc_id", "model"], set_={"embedding": stmt.excluded.embedding})
        )
        done += len(rows)
        session.commit()
    return done


# --- searching ---------------------------------------------------------------------------------------


@dataclass
class Hit:
    kind: str
    ref_id: uuid.UUID
    text: str
    at: datetime
    taint: str
    provider: str
    score: float


def tainted(hits: list[Hit]) -> bool:
    """Whether the turn read words it must not act on (a stranger's, a file's, the web's): from the `taint` column."""
    return any(h.taint in TAINTING for h in hits)


def hnsw_index_name(model: str) -> str:
    import hashlib

    return f"ix_recall_emb_hnsw_{hashlib.sha256(model.encode()).hexdigest()[:12]}"


def hnsw_dimension(session: Session, model: str) -> int | None:
    """The vector size of the approximate index for this model, if one exists (the query must cast to it)."""
    if not _has_recall_embedding(session):
        return None
    row = session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": hnsw_index_name(model)}
    ).scalar()
    if not row or "::vector(" not in row:
        return None
    return int(row.split("::vector(")[1].split(")")[0])


def search(
    session: Session,
    aud: Audience,
    query: str,
    *,
    kinds: tuple[str, ...] | None = None,
    rooms: bool = False,
    limit: int = 8,
    since: datetime | None = None,
    until: datetime | None = None,
    person_ids: set[uuid.UUID] | None = None,
    emb: Embedder | None = None,
) -> list[Hit]:
    """One statement: gate and filters, then the full-text, trigram and meaning arms, then reciprocal rank fusion, then
    the check that each candidate's source is still live (erased, retired, changed audience: hidden)."""
    words = lexicon.content_words(query)
    if not words:
        return []
    groups = lexicon.groups(query)
    gate = doc_filter(aud, rooms=rooms)  # the audience gate and the instance scope, in every arm

    def base(*cols: Any) -> Select[Any]:
        stmt = select(*cols).select_from(RecallDoc).where(gate)
        if kinds is not None:
            stmt = stmt.where(RecallDoc.kind.in_(kinds))
        if since is not None:
            stmt = stmt.where(RecallDoc.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(RecallDoc.occurred_at < until)
        if person_ids is not None:
            stmt = stmt.where(RecallDoc.said_by_person_id.in_(person_ids)) if person_ids else stmt.where(false())
        return stmt

    arm_limit = limit * 3
    arms: list[Select[Any]] = []
    for any_word in (False, True):
        q = _tsquery(groups, any_word=any_word)
        if q is None:
            continue
        rank = func.row_number().over(order_by=desc(func.ts_rank_cd(RecallDoc.tsv, q)))
        arms.append(
            select(base(RecallDoc.id, rank.label("r")).where(RecallDoc.tsv.op("@@")(q)).subquery()).limit(arm_limit)
        )
    long = [w for w in words if len(w) >= 5][:6]
    if long and _has_trgm(session):
        sims = [func.word_similarity(w, RecallDoc.text) for w in long]
        score = sims[0] if len(sims) == 1 else func.greatest(*sims)
        floor = case((RecallDoc.kind.in_(MESSAGE_KINDS), VISITOR_TRGM_MIN), else_=TRGM_MIN)
        rank = func.row_number().over(order_by=desc(score))
        arms.append(select(base(RecallDoc.id, rank.label("r")).where(score >= floor).subquery()).limit(arm_limit))
    arms.extend(_meaning_arm(session, base, query, arm_limit, emb))
    if not arms:
        return []
    ranked = union_all(*arms).subquery("arms")
    fused = (
        select(ranked.c.id, func.sum(1.0 / (RRF_K + cast(ranked.c.r, Float))).label("score"))
        .group_by(ranked.c.id)
        .order_by(desc("score"))
        .limit(limit * 3)  # a few spare: the liveness check below may drop a row whose source just changed
        .subquery("fused")
    )
    stmt = (
        select(RecallDoc, fused.c.score)
        .join(fused, fused.c.id == RecallDoc.id)
        .where(source_live())  # on the few candidates only, not on every row of every arm
        .order_by(desc(fused.c.score), desc(RecallDoc.occurred_at))
        .limit(limit)
    )
    return [
        Hit(d.kind, d.ref_id, d.text, d.occurred_at, d.taint, d.provider, float(score))
        for d, score in session.execute(stmt).all()
    ]


def _meaning_arm(session: Session, base: Any, query: str, limit: int, emb: Embedder | None) -> list[Select[Any]]:
    emb = emb or get_embedder(get_settings())
    if not emb.ready() or not _has_recall_embedding(session):
        return []
    vectors = emb.embed([query], kind="query")
    if not vectors:
        return []
    re = recall_embedding.c
    dim = hnsw_dimension(session, emb.name)
    column = cast(re.embedding, SizedVector(dim)) if dim and dim == len(vectors[0]) else re.embedding
    distance = column.op("<=>", return_type=Float())(cast(vectors[0], SizedVector(dim) if dim else Vector()))
    rank = func.row_number().over(order_by=distance)
    inner = (
        base(RecallDoc.id, rank.label("r"))
        .join(
            recall_embedding, and_(re.doc_id == RecallDoc.id, re.model == emb.name, re.agent_id == RecallDoc.agent_id)
        )
        .where(distance <= 1 - emb.min_similarity)
        .subquery()
    )
    return [select(inner).limit(limit)]


def visible_refs(session: Session, aud: Audience, *, rooms: bool = False) -> set[tuple[str, uuid.UUID]]:
    """Every (kind, source id) this audience may see through the index: the gate alone, no query. For the parity
    tests against today's per-source gates."""
    stmt = select(RecallDoc.kind, RecallDoc.ref_id).where(doc_filter(aud, rooms=rooms), source_live())
    return {(k, r) for k, r in session.execute(stmt).all()}


__all__ = [
    "KINDS",
    "Hit",
    "doc_filter",
    "embed_missing",
    "retire",
    "search",
    "source_live",
    "sync",
    "tainted",
    "visible_refs",
]
