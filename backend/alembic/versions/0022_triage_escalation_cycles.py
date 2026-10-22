"""triage escalation cycles: a snooze starts a new cycle of the ladder (ADR 0010 TR3)

Each delivery row records its cycle, so the same rung can be sent again after a snooze while a
retried job still finds (and skips) a rung it already sent in this cycle.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-22 06:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("escalation_delivery", sa.Column("cycle", sa.Integer(), server_default="0", nullable=False))
    op.drop_constraint(op.f("uq_escalation_delivery_step"), "escalation_delivery", type_="unique")
    op.create_unique_constraint(
        "uq_escalation_delivery_step", "escalation_delivery", ["escalation_id", "cycle", "step", "provider"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_escalation_delivery_step", "escalation_delivery", type_="unique")
    op.create_unique_constraint(
        op.f("uq_escalation_delivery_step"), "escalation_delivery", ["escalation_id", "step", "provider"]
    )
    op.drop_column("escalation_delivery", "cycle")
