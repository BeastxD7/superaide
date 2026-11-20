"""recall_doc and recall_embedding: one derived search index over notes, messages, visitor messages, passages,
episodes (#647)

Revision ID: 0107
Revises: 0106
Create Date: 2026-11-20 16:00:00.000000

`recall_doc` is a per-instance table under Row-Level Security, rebuilt from the source tables (they stay the truth).
`recall_embedding` mirrors `memory_embedding`: outside the ORM, created only where pgvector exists, RLS on.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0107"
down_revision: str | Sequence[str] | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("recall_doc", "recall_embedding")

EMBEDDING_SQL = """
CREATE TABLE IF NOT EXISTS recall_embedding (
    doc_id uuid NOT NULL REFERENCES recall_doc(id) ON DELETE CASCADE,
    model varchar(160) NOT NULL,
    tenant_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, model)
);
CREATE INDEX IF NOT EXISTS ix_recall_embedding_agent_model ON recall_embedding (agent_id, model);
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "recall_doc",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=False),
        sa.Column("said_by_person_id", sa.UUID(), nullable=True),
        sa.Column("said_by_identity_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("context_id", sa.UUID(), nullable=True),
        sa.Column("thread_id", sa.String(length=120), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_kind", sa.String(length=20), nullable=False),
        sa.Column("allowed_person_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("has_unknown", sa.Boolean(), nullable=False),
        sa.Column("min_trust", sa.Integer(), nullable=False),
        sa.Column("taint", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "kind", "ref_id", name="uq_recall_doc_source"),
    )
    op.create_index(op.f("ix_recall_doc_agent_id"), "recall_doc", ["agent_id"], unique=False)
    op.create_index(op.f("ix_recall_doc_tenant_id"), "recall_doc", ["tenant_id"], unique=False)
    op.create_index(
        "ix_recall_doc_agent_kind_at", "recall_doc", ["agent_id", "kind", sa.text("occurred_at DESC")], unique=False
    )
    op.create_index(
        "ix_recall_doc_agent_person", "recall_doc", ["agent_id", "said_by_person_id", "occurred_at"], unique=False
    )
    op.create_index("ix_recall_doc_tsv", "recall_doc", ["tsv"], unique=False, postgresql_using="gin")
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")):
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(EMBEDDING_SQL)
    from superaide.db.rls import enable

    enable(conn, TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.execute("DROP TABLE IF EXISTS recall_embedding")
    op.drop_index("ix_recall_doc_tsv", table_name="recall_doc", postgresql_using="gin")
    op.drop_index("ix_recall_doc_agent_person", table_name="recall_doc")
    op.drop_index("ix_recall_doc_agent_kind_at", table_name="recall_doc")
    op.drop_index(op.f("ix_recall_doc_tenant_id"), table_name="recall_doc")
    op.drop_index(op.f("ix_recall_doc_agent_id"), table_name="recall_doc")
    op.drop_table("recall_doc")
