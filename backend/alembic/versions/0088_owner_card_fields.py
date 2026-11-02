"""owner_preference: the share card, typed values with a visibility switch each, off by default (#475)

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0088"
down_revision: str | Sequence[str] | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# owner_preference already carries tenant_id, agent_id and its row-level-security policy; new columns inherit both.
TEXTS = (("card_company", 120), ("card_role", 120), ("card_city", 120), ("card_email", 200))
SWITCHES = ("card_company_public", "card_role_public", "card_city_public", "card_email_public")


def upgrade() -> None:
    for name, size in TEXTS:
        op.add_column("owner_preference", sa.Column(name, sa.String(length=size), server_default="", nullable=False))
        op.alter_column("owner_preference", name, server_default=None)
    for name in SWITCHES:
        op.add_column("owner_preference", sa.Column(name, sa.Boolean(), server_default=sa.false(), nullable=False))
        op.alter_column("owner_preference", name, server_default=None)


def downgrade() -> None:
    for name in (*SWITCHES, *(n for n, _ in TEXTS)):
        op.drop_column("owner_preference", name)
