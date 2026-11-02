"""notification_delivery: did each push to the owner's Telegram or Slack arrive, and when is the next try (#491)

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-02 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0085"
down_revision: str | Sequence[str] | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("notification_delivery",)


def upgrade() -> None:
    op.create_table(
        "notification_delivery",
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_delivery_agent_id"), "notification_delivery", ["agent_id"], unique=False)
    op.create_index(op.f("ix_notification_delivery_tenant_id"), "notification_delivery", ["tenant_id"], unique=False)
    op.create_index("ix_notification_delivery_state", "notification_delivery", ["agent_id", "state"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    from superaide.db.rls import disable

    disable(op.get_bind(), TABLES)
    op.drop_index("ix_notification_delivery_state", table_name="notification_delivery")
    op.drop_index(op.f("ix_notification_delivery_tenant_id"), table_name="notification_delivery")
    op.drop_index(op.f("ix_notification_delivery_agent_id"), table_name="notification_delivery")
    op.drop_table("notification_delivery")
