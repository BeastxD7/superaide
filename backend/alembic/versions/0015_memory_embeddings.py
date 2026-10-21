"""memory embeddings: pgvector and the memory_embedding table, where pgvector exists

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-22 18:00:00

The table lives outside the ORM metadata (context/vectors.py): a server without pgvector
gets no table, and recall stays keyword-only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS memory_embedding (
    memory_id uuid NOT NULL REFERENCES memory_item(id) ON DELETE CASCADE,
    model varchar(160) NOT NULL,
    tenant_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, model)
);
CREATE INDEX IF NOT EXISTS ix_memory_embedding_agent_model ON memory_embedding (agent_id, model);
"""


def upgrade() -> None:
    conn = op.get_bind()
    if not conn.scalar(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")):
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_embedding")
