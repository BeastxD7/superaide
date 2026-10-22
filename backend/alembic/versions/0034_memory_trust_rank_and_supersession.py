"""memory trust rank, author, supersession and conflict status (issue #46)

Revision ID: 0034
Revises: 0030
Create Date: 2026-10-23 09:00:00

A newer fact replaces an older one only when its trust rank is equal or better; a weaker claim waits
as a conflict for the owner. Existing facts get the rank their source implies.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("trust_rank", sa.Integer(), server_default="1", nullable=False))
    op.add_column("memory_item", sa.Column("author_person_id", sa.UUID(), nullable=True))
    op.add_column("memory_item", sa.Column("superseded_by", sa.UUID(), nullable=True))
    op.add_column(
        "memory_item", sa.Column("conflict_status", sa.String(length=10), server_default="none", nullable=False)
    )
    op.add_column("memory_item", sa.Column("conflicts_with_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_memory_item_author", "memory_item", "person", ["author_person_id"], ["id"])
    op.create_foreign_key("fk_memory_item_superseded_by", "memory_item", "memory_item", ["superseded_by"], ["id"])
    op.create_foreign_key("fk_memory_item_conflicts_with", "memory_item", "memory_item", ["conflicts_with_id"], ["id"])
    op.execute("UPDATE memory_item SET trust_rank = 2 WHERE source = 'document'")
    op.execute("UPDATE memory_item SET trust_rank = 3 WHERE source = 'link'")
    op.execute(
        "UPDATE memory_item SET author_person_id = subject_person_ids[1] WHERE cardinality(subject_person_ids) = 1"
    )


def downgrade() -> None:
    op.drop_constraint("fk_memory_item_conflicts_with", "memory_item", type_="foreignkey")
    op.drop_constraint("fk_memory_item_superseded_by", "memory_item", type_="foreignkey")
    op.drop_constraint("fk_memory_item_author", "memory_item", type_="foreignkey")
    for col in ("conflicts_with_id", "conflict_status", "superseded_by", "author_person_id", "trust_rank"):
        op.drop_column("memory_item", col)
