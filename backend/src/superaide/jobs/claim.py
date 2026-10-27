"""Claiming due jobs fairly across agent instances (ADR 0009 T5).

The worker serves every instance, but the app's own database role only sees the rows
of the instance in scope (Row-Level Security). So claiming goes through one narrow
function, `claim_due_jobs`, that runs as the schema owner (SECURITY DEFINER): it
marks due jobs as running and returns only their ids, kinds and instances. Each job
then runs inside its own instance's scope.

Fairness: due jobs are ranked within each instance by when they were due, and the
batch takes every instance's first job before anyone's second (at most `per_instance`
each), so one owner with a thousand reminders can't hold up another owner's one.

The function is created by migration 0022 and, for schemas built with create_all
(tests, the eval harness, `superaide init-db`), right after the `job` table.
The schema owner must be a superuser or BYPASSRLS (as Postgres' own user is in the
compose files), since the job table forces Row-Level Security even on its owner.
"""

from __future__ import annotations

from sqlalchemy import DDL, Connection, event

from superaide.jobs.models import Job

CREATE = """
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

DROP = "DROP FUNCTION IF EXISTS claim_due_jobs(timestamptz, integer, integer, interval)"


def grant_to(conn: Connection, role: str) -> None:
    conn.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION claim_due_jobs(timestamptz, integer, integer, interval) TO {role}")


def create(conn: Connection) -> None:
    conn.exec_driver_sql(CREATE)


# Schemas made with create_all get the function too.
event.listen(Job.__table__, "after_create", DDL(CREATE))
