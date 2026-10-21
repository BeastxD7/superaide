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
from superaide.config import get_settings
from superaide.db.models import Job, MemoryItem
from superaide.jobs.queue import enqueue, handler

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


def ensure_vector_store(engine: Engine) -> bool:
    """Install pgvector and the embeddings table if the server has pgvector. False if not."""
    with engine.begin() as conn:
        if not conn.scalar(text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")):
            return False
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(CREATE_SQL))
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
            and_(me.memory_id == MemoryItem.id, me.model == emb.name, me.agent_id == get_settings().agent_id),
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
    settings = get_settings()
    me = memory_embedding.c
    done = 0
    for _ in range(max_rounds):
        rows = session.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.valid_to.is_(None),
                MemoryItem.agent_id == settings.agent_id,
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
