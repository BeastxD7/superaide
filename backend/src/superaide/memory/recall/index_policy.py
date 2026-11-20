"""When exact search stops being enough (#647 item 4, report 7.1): a partial HNSW index per embedding model.

Exact scan is the default: 8 ms over one owner's 5,000 rows, 58 ms over 100,000 (report 6.4). One instance that passes
`ROW_THRESHOLD` embedded rows for a model gets an approximate index for that model, on a cast to the model's own
dimension so models of different sizes can share the table. The row count, the threshold and the measured p95 of an
exact scan before the index go in the audit log (the existing store), so the decision can be reviewed.

`search` uses the index only when it is there and the cast matches (`index.hnsw_dimension`); until then, and for any
model without one, it is the exact scan.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from superaide.jobs.models import Job
from superaide.jobs.queue import handler
from superaide.memory.recall import index
from superaide.memory.recall.vectors import RECALL_TABLE
from superaide.platform.audit import audit
from superaide.platform.db.session import get_engine
from superaide.platform.tenancy.context import scope_or_default

log = logging.getLogger(__name__)

JOB_KIND = "recall_index_policy"
ROW_THRESHOLD = 200_000  # embedded rows of one model in one instance (report 7.1: "about 200,000")
PROBES = 20
MAX_DIM = 2000  # pgvector's limit for an indexed `vector`


def p95_ms(session: Session, model: str, dim: int) -> float:
    """p95 of an exact top-12 scan for this instance and model, over probes drawn from its own vectors."""
    agent = scope_or_default().agent_id
    probes = (
        session.execute(
            text(
                f"SELECT embedding::text FROM {RECALL_TABLE} WHERE agent_id = :a AND model = :m "
                "ORDER BY random() LIMIT :n"
            ),
            {"a": agent, "m": model, "n": PROBES},
        )
        .scalars()
        .all()
    )
    times: list[float] = []
    for probe in probes:
        t0 = time.perf_counter()
        session.execute(
            text(
                f"SELECT doc_id FROM {RECALL_TABLE} WHERE agent_id = :a AND model = :m "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 12"
            ),
            {"a": agent, "m": model, "q": probe},
        ).all()
        times.append((time.perf_counter() - t0) * 1000)
    if not times:
        return 0.0
    times.sort()
    return times[min(len(times) - 1, int(len(times) * 0.95))]


def ensure_index(session: Session, model: str, dim: int) -> bool:
    """Create the partial HNSW index for `model` (CONCURRENTLY, so writes carry on; that needs its own autocommit
    connection). Returns whether it exists afterwards. A role that may not create it just logs and stays exact."""
    if dim > MAX_DIM:
        return False
    name = index.hnsw_index_name(model)
    quoted = model.replace("'", "''")
    ddl = (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {RECALL_TABLE} "
        f"USING hnsw ((embedding::vector({int(dim)})) vector_cosine_ops) WHERE model = '{quoted}'"
    )
    try:
        with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(ddl))
    except Exception:
        log.exception("couldn't create the approximate index for %s; search stays exact", model)
        return False
    return index.hnsw_dimension(session, model) == dim


def apply(session: Session, threshold: int | None = None) -> list[str]:
    """For each model whose embedded rows in this instance reach the threshold, record the numbers and build its index.
    Returns the models that got one."""
    threshold = ROW_THRESHOLD if threshold is None else threshold
    if not index._has_recall_embedding(session):
        return []
    agent = scope_or_default().agent_id
    rows = session.execute(
        text(
            f"SELECT model, count(*) AS n, min(vector_dims(embedding)) AS dim FROM {RECALL_TABLE} "
            "WHERE agent_id = :a GROUP BY model"
        ),
        {"a": agent},
    ).all()
    made: list[str] = []
    for model, n, dim in rows:
        if n < threshold or index.hnsw_dimension(session, model) == dim:
            continue
        measured = p95_ms(session, model, dim)
        session.commit()  # the audit line must not wait on the long build
        built = ensure_index(session, model, dim)
        audit(
            session, "recall", "approximate_index", model=model, rows=n, threshold=threshold, p95_ms=round(measured, 1),
            dimension=dim, built=built,
        )  # fmt: skip
        if built:
            made.append(model)
    return made


@handler(JOB_KIND)
def run(session: Session, job: Job) -> None:
    made = apply(session)
    if made:
        log.info("approximate recall index built for %s", ", ".join(made))
