"""a key per model endpoint, so one Ava can have several of the same kind

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-22 22:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_provider", sa.Column("key", sa.String(length=24), nullable=True))
    op.execute("UPDATE llm_provider SET key = preset")  # the existing endpoint keeps its preset as its key
    op.alter_column("llm_provider", "key", nullable=False)
    op.drop_constraint("uq_llm_provider", "llm_provider", type_="unique")
    op.create_unique_constraint("uq_llm_provider_key", "llm_provider", ["agent_id", "key"])


def downgrade() -> None:
    op.execute("DELETE FROM llm_provider WHERE key <> preset")
    op.drop_constraint("uq_llm_provider_key", "llm_provider", type_="unique")
    op.create_unique_constraint("uq_llm_provider", "llm_provider", ["agent_id", "preset"])
    op.drop_column("llm_provider", "key")
