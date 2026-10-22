"""triage escalation cycles: a snooze starts a new cycle of the ladder (ADR 0010 TR3)

Each delivery row records its cycle, so the same rung can be sent again after a snooze while a
retried job still finds (and skips) a rung it already sent in this cycle.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-22 06:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("escalation_delivery", sa.Column("cycle", sa.Integer(), server_default="0", nullable=False))
    op.drop_constraint(op.f("uq_escalation_delivery_step"), "escalation_delivery", type_="unique")
    op.create_unique_constraint(
        "uq_escalation_delivery_step", "escalation_delivery", ["escalation_id", "cycle", "step", "provider"]
    )


def downgrade() -> None:
    # A snooze sent the same rung again in a later cycle; the old index allows one row per rung.
    # Keep the latest cycle's row (the most recent send) and drop the earlier ones, so this succeeds.
    op.execute(
        "DELETE FROM escalation_delivery d USING escalation_delivery newer "
        "WHERE d.escalation_id = newer.escalation_id AND d.step = newer.step AND d.provider = newer.provider "
        "AND (d.cycle < newer.cycle OR (d.cycle = newer.cycle AND d.id < newer.id))"
    )
    op.drop_constraint("uq_escalation_delivery_step", "escalation_delivery", type_="unique")
    op.create_unique_constraint(
        op.f("uq_escalation_delivery_step"), "escalation_delivery", ["escalation_id", "step", "provider"]
    )
    op.drop_column("escalation_delivery", "cycle")
