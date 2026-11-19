"""recall_indexes: indexes for recall by time, sender and words (#644)

Revision ID: 0100
Revises: 0099
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0100"
down_revision: str | Sequence[str] | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Indexes only: no row changes, no new table."""
    op.create_index("ix_reception_message_agent_created", "reception_message", ["agent_id", "created_at"])
    op.execute("CREATE INDEX ix_reception_message_fts ON reception_message USING gin (to_tsvector('english', message))")
    op.create_index("ix_event_agent_person_created", "event", ["agent_id", "resolved_person_id", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_event_agent_person_created", table_name="event")
    op.drop_index("ix_reception_message_fts", table_name="reception_message")
    op.drop_index("ix_reception_message_agent_created", table_name="reception_message")
