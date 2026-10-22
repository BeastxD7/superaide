"""jobs per instance: dedupe keys per instance, and the fair cross-instance claim (ADR 0009 T5)

`claim_due_jobs` lets the worker, connected as the app's own role, claim due jobs of
every instance (each then runs in its own instance's scope); see jobs/claim.py.

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
    from superaide.jobs.claim import create, grant_to

    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS job_dedupe_key_key")
    op.create_unique_constraint("uq_job_dedupe", "job", ["agent_id", "dedupe_key"])
    conn = op.get_bind()
    create(conn)
    if conn.scalar(sa.text("SELECT count(*) FROM pg_roles WHERE rolname = 'superaide_app'")):
        grant_to(conn, "superaide_app")


def downgrade() -> None:
    from superaide.jobs.claim import DROP

    op.execute(DROP)
    op.drop_constraint("uq_job_dedupe", "job", type_="unique")
    op.create_unique_constraint("job_dedupe_key_key", "job", ["dedupe_key"])
