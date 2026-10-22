"""document chunks: passages of each shared file or page, searchable end to end (issue #47)

Revision ID: 0036
Revises: 0034
Create Date: 2026-10-23 12:00:00

`document_chunk` is a per-instance table under Row-Level Security. `chunk_embedding` mirrors
`memory_embedding` (outside the ORM, only where pgvector exists). Existing documents are split here.
"""

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIZE = 1500


def _split(text: str) -> list[str]:  # a frozen copy of knowledge/chunking.split, so this migration never changes
    out: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > SIZE:
            cut = max(para.rfind(". ", 0, SIZE), para.rfind("\n", 0, SIZE), para.rfind(" ", 0, SIZE))
            cut = cut + 1 if cut > SIZE // 3 else SIZE
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 > SIZE:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return [c for c in out if c][:400]


def upgrade() -> None:
    op.create_table(
        "document_chunk",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_document_chunk_ordinal"),
    )
    op.create_index(op.f("ix_document_chunk_agent_id"), "document_chunk", ["agent_id"], unique=False)
    op.create_index(op.f("ix_document_chunk_tenant_id"), "document_chunk", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_document_chunk_document_id"), "document_chunk", ["document_id"], unique=False)
    op.create_index(
        "ix_document_chunk_fts", "document_chunk", [sa.text("to_tsvector('english', text)")], postgresql_using="gin"
    )
    op.add_column("memory_item", sa.Column("chunk_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_memory_item_chunk", "memory_item", "document_chunk", ["chunk_id"], ["id"], ondelete="SET NULL"
    )

    conn = op.get_bind()
    chunk = sa.table(
        "document_chunk",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("document_id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.column("agent_id", postgresql.UUID(as_uuid=True)),
        sa.column("ordinal", sa.Integer),
        sa.column("text", sa.Text),
    )
    for doc_id, tenant, agent, text in conn.execute(
        sa.text("SELECT id, tenant_id, agent_id, text FROM document WHERE removed_at IS NULL AND text <> ''")
    ).all():
        rows = [
            {"id": uuid.uuid4(), "document_id": doc_id, "tenant_id": tenant, "agent_id": agent, "ordinal": i, "text": t}
            for i, t in enumerate(_split(text))
        ]
        if rows:
            conn.execute(sa.insert(chunk), rows)

    if conn.scalar(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")):
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_embedding (
                chunk_id uuid NOT NULL REFERENCES document_chunk(id) ON DELETE CASCADE,
                model varchar(160) NOT NULL,
                tenant_id uuid NOT NULL,
                agent_id uuid NOT NULL,
                embedding vector NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (chunk_id, model)
            );
            CREATE INDEX IF NOT EXISTS ix_chunk_embedding_agent_model ON chunk_embedding (agent_id, model);
            """
        )
    from superaide.db import rls

    rls.enable(conn, ["document_chunk", "chunk_embedding"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunk_embedding")
    op.drop_constraint("fk_memory_item_chunk", "memory_item", type_="foreignkey")
    op.drop_column("memory_item", "chunk_id")
    op.drop_table("document_chunk")
