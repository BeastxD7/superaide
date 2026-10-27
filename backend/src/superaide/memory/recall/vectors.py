"""Where fact embeddings live and how recall searches them (ADR 0008 WP2b).

Vectors sit in their own table, `memory_embedding`, one row per (fact, model), so switching
embedder is a background re-embed rather than a migration, and an install without pgvector
(the embedded no-Docker Postgres, say) simply has no table: recall then stays keyword-only.
The table is outside the ORM metadata for that reason (alembic's env skips it).

The meaning-search arm is built on the same gated query as every other recall arm, so the
audience gate (memory_filter) and this Ava instance's agent_id are in its SQL too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Float,
    MetaData,
    Select,
    String,
    Table,
    and_,
    cast,
    exists,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

from superaide.brain.embed import Embedder, LocalEmbedder, get_embedder
from superaide.jobs.models import Job
from superaide.jobs.queue import enqueue, handler
from superaide.memory.models import DocumentChunk, MemoryItem
from superaide.platform.config import get_settings
from superaide.platform.tenancy.context import scope_or_default

log = logging.getLogger(__name__)

TABLE = "memory_embedding"
JOB_KIND = "embed_memories"
BATCH = 64


class Vector(UserDefinedType[list[float]]):
    """pgvector's `vector`, without a fixed size (each model's rows share one size)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"

    def bind_processor(self, dialect: Any) -> Callable[[Any], Any]:
        def process(value: Any) -> Any:
            return None if value is None else "[" + ",".join(f"{float(x):.7g}" for x in value) + "]"

        return process


_meta = MetaData()
memory_embedding = Table(
    TABLE,
    _meta,
    Column("memory_id", UUID(as_uuid=True), primary_key=True),
    Column("model", String(160), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("embedding", Vector(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
    model varchar(160) NOT NULL,
    tenant_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, model)
);
CREATE INDEX IF NOT EXISTS ix_{TABLE}_agent_model ON {TABLE} (agent_id, model);
"""


CHUNK_TABLE = "chunk_embedding"
chunk_embedding = Table(
    CHUNK_TABLE,
    _meta,
    Column("chunk_id", UUID(as_uuid=True), primary_key=True),
    Column("model", String(160), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("embedding", Vector(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

CHUNK_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {CHUNK_TABLE} (
    chunk_id uuid NOT NULL REFERENCES document_chunk(id) ON DELETE CASCADE,
    model varchar(160) NOT NULL,
    tenant_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model)
);
CREATE INDEX IF NOT EXISTS ix_{CHUNK_TABLE}_agent_model ON {CHUNK_TABLE} (agent_id, model);
"""


def ensure_vector_store(engine: Engine) -> bool:
    """Install pgvector and the embeddings table if the server has pgvector. False if not."""
    with engine.begin() as conn:
        if not conn.scalar(text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")):
            return False
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(CREATE_SQL))
        conn.execute(text(CHUNK_CREATE_SQL))
    return True


def vectors_ready(session: Session) -> bool:
    """Whether this database has the embeddings table (a catalogue lookup, microseconds)."""
    return session.scalar(text(f"SELECT to_regclass('{TABLE}') IS NOT NULL")) is True


def embedder() -> Embedder:
    return get_embedder(get_settings())


# --- search ------------------------------------------------------------------------


def meaning_arm(session: Session, base: Callable[[], Select[Any]], query: str, limit: int) -> list[MemoryItem]:
    """Facts closest in meaning to the query, from the gated `base()` query; [] when the
    embedder or the table isn't there (keyword search carries on alone)."""
    emb = embedder()
    if not query.strip() or not emb.ready() or not vectors_ready(session):
        return []
    vectors = emb.embed([query], kind="query")
    if not vectors:
        return []
    me = memory_embedding.c
    distance = me.embedding.op("<=>", return_type=Float())(cast(vectors[0], Vector()))
    stmt = (
        base()
        .join(
            memory_embedding,
            and_(me.memory_id == MemoryItem.id, me.model == emb.name, me.agent_id == scope_or_default().agent_id),
        )
        .where(distance <= 1 - emb.min_similarity)
        .order_by(distance)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def chunk_meaning_arm(session: Session, base: Callable[[], Select[Any]], query: str, limit: int) -> list[DocumentChunk]:
    """Passages closest in meaning to the query, from the gated `base()` query over DocumentChunk."""
    emb = embedder()
    if (
        not query.strip()
        or not emb.ready()
        or not session.scalar(text(f"SELECT to_regclass('{CHUNK_TABLE}') IS NOT NULL"))
    ):
        return []
    vectors = emb.embed([query], kind="query")
    if not vectors:
        return []
    ce = chunk_embedding.c
    distance = ce.embedding.op("<=>", return_type=Float())(cast(vectors[0], Vector()))
    stmt = (
        base()
        .join(
            chunk_embedding,
            and_(ce.chunk_id == DocumentChunk.id, ce.model == emb.name, ce.agent_id == scope_or_default().agent_id),
        )
        .where(distance <= 1 - emb.min_similarity)
        .order_by(distance)
        .limit(limit)
    )
    return list(session.scalars(stmt))


# --- writing -----------------------------------------------------------------------


def schedule(session: Session, item: MemoryItem) -> None:
    """Embed a new fact soon, in the background."""
    if embedder().name != "none":
        enqueue(session, JOB_KIND, {}, dedupe_key=f"embed:{item.id}", max_attempts=3)


def schedule_chunks(session: Session, document_id: Any) -> None:
    """Embed a new document's passages soon, in the background."""
    if embedder().name != "none":
        enqueue(session, JOB_KIND, {}, dedupe_key=f"embed_chunks:{document_id}", max_attempts=3)


def start(session: Session) -> None:
    """At startup: begin loading the local model and queue a backfill for this model."""
    emb = embedder()
    if emb.name == "none":
        return
    emb.warm()
    enqueue(session, JOB_KIND, {"backfill": True}, dedupe_key=f"embed_backfill:{emb.name}"[:200], max_attempts=3)


def embed_missing(session: Session, max_rounds: int = 20) -> int:
    """Embed this Ava's live facts that have no vector for the current model. Returns how many."""
    emb = embedder()
    if emb.name == "none" or not vectors_ready(session):
        return 0
    me = memory_embedding.c
    done = 0
    for _ in range(max_rounds):
        rows = session.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.valid_to.is_(None),
                MemoryItem.agent_id == scope_or_default().agent_id,
                ~exists().where(me.memory_id == MemoryItem.id, me.model == emb.name),
            )
            .order_by(MemoryItem.created_at)
            .limit(BATCH)
        ).all()
        if not rows:
            break
        vectors = emb.embed([m.content for m in rows], kind="document")
        if not vectors:
            break
        now = datetime.now(UTC)
        stmt = insert(memory_embedding).values(
            [
                {"memory_id": m.id, "model": emb.name, "tenant_id": m.tenant_id, "agent_id": m.agent_id,
                 "embedding": v, "created_at": now}
                for m, v in zip(rows, vectors, strict=True)
            ]
        )  # fmt: skip
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["memory_id", "model"], set_={"embedding": stmt.excluded.embedding}
            )
        )
        done += len(rows)
    return done + _embed_missing_chunks(session, emb)


def _embed_missing_chunks(session: Session, emb: Embedder) -> int:
    from superaide.memory.models import Document

    if not session.scalar(text(f"SELECT to_regclass('{CHUNK_TABLE}') IS NOT NULL")):
        return 0
    ce = chunk_embedding.c
    done = 0
    for _ in range(20):
        rows = session.scalars(
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.removed_at.is_(None),
                DocumentChunk.agent_id == scope_or_default().agent_id,
                ~exists().where(ce.chunk_id == DocumentChunk.id, ce.model == emb.name),
            )
            .order_by(DocumentChunk.created_at, DocumentChunk.ordinal)
            .limit(BATCH)
        ).all()
        if not rows:
            break
        vectors = emb.embed([c.text for c in rows], kind="document")
        if not vectors:
            break
        now = datetime.now(UTC)
        stmt = insert(chunk_embedding).values(
            [
                {"chunk_id": c.id, "model": emb.name, "tenant_id": c.tenant_id, "agent_id": c.agent_id,
                 "embedding": v, "created_at": now}
                for c, v in zip(rows, vectors, strict=True)
            ]
        )  # fmt: skip
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["chunk_id", "model"], set_={"embedding": stmt.excluded.embedding}
            )
        )
        done += len(rows)
    return done


@handler(JOB_KIND)
def run(session: Session, job: Job) -> None:
    emb = embedder()
    if isinstance(emb, LocalEmbedder) and emb.failed:
        return  # logged when it failed; recall stays keyword-only
    if isinstance(emb, LocalEmbedder) and not emb.ready():
        # The model is still downloading or loading: never block the job worker on it. Look again soon.
        emb.warm()
        when = datetime.now(UTC) + timedelta(seconds=30)
        enqueue(session, JOB_KIND, {}, run_at=when, dedupe_key=f"embed:wait:{when:%Y%m%d%H%M}")
        return
    n = embed_missing(session)
    if n:
        log.info("embedded %d facts with %s", n, emb.name)
