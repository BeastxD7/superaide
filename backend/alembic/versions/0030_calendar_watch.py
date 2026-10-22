"""calendar_watch: Google Calendar push channels, per instance

One row per events.watch channel on an owner's primary calendar (nothing writes it unless
GOOGLE_CALENDAR_PUSH is on). Per-instance table under the Row-Level Security policy (ADR 0009).

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-22 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "calendar_watch",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", name="uq_calendar_watch_channel"),
    )
    op.create_index(op.f("ix_calendar_watch_agent_id"), "calendar_watch", ["agent_id"], unique=False)
    op.create_index(op.f("ix_calendar_watch_owner_person_id"), "calendar_watch", ["owner_person_id"], unique=False)
    op.create_index(op.f("ix_calendar_watch_tenant_id"), "calendar_watch", ["tenant_id"], unique=False)
    from superaide.db.rls import enable

    enable(op.get_bind(), ["calendar_watch"])


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), ["calendar_watch"])
    op.drop_index(op.f("ix_calendar_watch_tenant_id"), table_name="calendar_watch")
    op.drop_index(op.f("ix_calendar_watch_owner_person_id"), table_name="calendar_watch")
    op.drop_index(op.f("ix_calendar_watch_agent_id"), table_name="calendar_watch")
    op.drop_table("calendar_watch")
