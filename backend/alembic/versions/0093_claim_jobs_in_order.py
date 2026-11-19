"""claim_due_jobs returns its claims in fair order (#593)

`UPDATE ... RETURNING` comes back in the order the planner joined the rows, so with a merge join the batch
ran in random-uuid order instead of rank order. The function now sorts what it returns.

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-19 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0093"
down_revision: str | Sequence[str] | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = """
CREATE OR REPLACE FUNCTION claim_due_jobs(p_now timestamptz, p_limit integer, p_per_instance integer,
                                          p_stale interval)
RETURNS TABLE (id uuid, kind text, tenant_id uuid, agent_id uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH due AS (
    SELECT j.id, j.agent_id, j.run_at,
           row_number() OVER (PARTITION BY j.agent_id ORDER BY j.run_at, j.id) AS rank
      FROM job j
     WHERE (j.status = 'queued' AND j.run_at <= p_now)
        OR (j.status = 'running' AND j.locked_at < p_now - p_stale)
  ), picked AS (
    SELECT j.id
      FROM job j JOIN due d ON d.id = j.id
     WHERE d.rank <= p_per_instance
     ORDER BY d.rank, d.run_at
     LIMIT p_limit
       FOR UPDATE OF j SKIP LOCKED
  )
  UPDATE job SET status = 'running', locked_at = p_now, attempts = job.attempts + 1
    FROM picked
   WHERE job.id = picked.id
  RETURNING job.id, job.kind::text, job.tenant_id, job.agent_id
$$;
REVOKE ALL ON FUNCTION claim_due_jobs(timestamptz, integer, integer, interval) FROM PUBLIC;
"""


def upgrade() -> None:
    from superaide.jobs.claim import create

    create(op.get_bind())  # CREATE OR REPLACE: same signature, ordered result; grants stay


def downgrade() -> None:
    op.get_bind().exec_driver_sql(PREVIOUS)
