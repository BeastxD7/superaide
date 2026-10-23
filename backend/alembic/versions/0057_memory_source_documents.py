"""memory_item.source_document_ids: further documents that support a fact (issue #48)

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-23 14:00:00

Erasing a document keeps a fact another document (or the owner's own words) still supports.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column("source_document_ids", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("memory_item", "source_document_ids")
