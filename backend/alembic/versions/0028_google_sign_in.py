"""google sign-in: the Google account id on each account

user_account.google_sub holds Google's stable account id ("sub") once someone signs in with
Google; unique, so one Google account opens one account. Accounts are platform-wide (not under
Row-Level Security), as before.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("user_account", sa.Column("google_sub", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_user_google_sub", "user_account", ["google_sub"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_user_google_sub", "user_account", type_="unique")
    op.drop_column("user_account", "google_sub")
