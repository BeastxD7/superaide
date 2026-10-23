"""mandate delegation: a mandate can be handed on, narrower, and revoking the parent revokes the children (#112)

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068"
down_revision: str | Sequence[str] | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mandate", sa.Column("parent_id", sa.UUID(), nullable=True))
    op.add_column("mandate", sa.Column("label", sa.String(length=120), server_default="", nullable=False))
    op.create_index(op.f("ix_mandate_parent_id"), "mandate", ["parent_id"], unique=False)
    op.create_foreign_key("fk_mandate_parent", "mandate", "mandate", ["parent_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_mandate_parent", "mandate", type_="foreignkey")
    op.drop_index(op.f("ix_mandate_parent_id"), table_name="mandate")
    op.drop_column("mandate", "label")
    op.drop_column("mandate", "parent_id")
