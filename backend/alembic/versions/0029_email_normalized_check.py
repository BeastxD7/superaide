"""user_account.email_normalized always matches email

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-23 10:00:00

Fixes rows where the two columns drifted (one was edited by hand), then adds a CHECK so it
can't happen again for plain ASCII addresses. A fix that would collide with another account's
address is refused with a message rather than guessed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATCH = "octet_length(email) <> char_length(email) OR email_normalized = lower(btrim(email))"


def upgrade() -> None:
    conn = op.get_bind()
    clash = conn.execute(
        sa.text(
            "SELECT a.id FROM user_account a JOIN user_account b ON b.id <> a.id "
            "AND b.email_normalized = lower(btrim(a.email)) "
            "WHERE NOT (octet_length(a.email) <> char_length(a.email) OR a.email_normalized = lower(btrim(a.email)))"
        )
    ).first()
    if clash is not None:
        raise RuntimeError(
            f"user_account {clash[0]}: its email, lower-cased, belongs to another account; fix it by hand first"
        )
    conn.execute(sa.text(f"UPDATE user_account SET email_normalized = lower(btrim(email)) WHERE NOT ({MATCH})"))
    op.create_check_constraint("ck_user_email_normalized", "user_account", MATCH)


def downgrade() -> None:
    op.drop_constraint("ck_user_email_normalized", "user_account", type_="check")
