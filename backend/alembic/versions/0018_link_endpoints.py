"""link endpoints: bind each channel link (and invite) to the bot or app that confirmed it

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22 03:00:00

Existing linked Telegram accounts are bound to the bot connected now (identity/binding.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel_identity", sa.Column("endpoint_key", sa.String(length=200), nullable=False, server_default="")
    )
    op.add_column("channel_invite", sa.Column("endpoint_key", sa.String(length=200), nullable=False, server_default=""))
    op.add_column("channel_invite", sa.Column("for_identity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "channel_invite_for_identity_id_fkey", "channel_invite", "channel_identity", ["for_identity_id"], ["id"]
    )

    from superaide.identity.binding import backfill

    conn = op.get_bind()
    # Row-level security (0017) hides every row when no instance is in scope, and FORCE applies it to
    # the schema owner too: lift it for this one update, inside this transaction.
    forced = conn.scalar(sa.text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'channel_identity'::regclass"))
    if forced:
        op.execute("ALTER TABLE channel_identity NO FORCE ROW LEVEL SECURITY")
    backfill(conn)
    if forced:
        op.execute("ALTER TABLE channel_identity FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_constraint("channel_invite_for_identity_id_fkey", "channel_invite", type_="foreignkey")
    op.drop_column("channel_invite", "for_identity_id")
    op.drop_column("channel_invite", "endpoint_key")
    op.drop_column("channel_identity", "endpoint_key")
