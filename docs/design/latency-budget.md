# Latency budget of a direct-message turn

Status: built for #413. Builds on the per-stage timing of #412 (`observability/timing.py`, p50 and p95 in the console).

## The budget
`backend/src/superaide/observability/budget.py` holds one line per pipeline stage, the whole-turn line and the model-call
line. A default DM turn with the fake model must stay inside all of them, and `tests/observability/test_latency_budget.py`
fails CI when it does not.

| Stage | Budget | Measured on main |
|---|---|---|
| identity | 16 | 10 |
| gate | 8 | 0 |
| retrieval | 8 | 5 |
| model | 4 | 0 |
| reply | 4 | 2 |
| send | 4 | 0 |
| whole turn | 45 | 28 |
| model calls | 1 | 1 |

Unit: one millisecond per database statement on a hand-driven clock. Wall-clock time is too noisy to gate a build on;
the cost that regresses silently is more work per stage (an N+1 query, a new lookup), and that is what the count catches,
exactly and identically on every machine. The model, the network and the channel send are fixed fakes. Real timings come
from `turn_timing` (console, Latency).

Raising a line is a decision for the pull request that does it, with the reason in its description. Heavy extras (second
opinion, dissent) are not on the default path; the test pins one model call.

## What was not done: running stages in parallel
Deliberately deferred, because it cannot be shown to be identity- and privacy-neutral:
* Retrieval is not independent of the audience gate. Which memories may be read is decided by the audience the gate
  computes (`context/audience.py`, filtered inside the SQL), so retrieval needs the gate's result.
* A turn runs on one SQLAlchemy session and one transaction (RLS scope is set per transaction). A session is not safe to use
  from two threads, and a second session would not see the first one's uncommitted rows, which changes what the later
  stages can read.
* The timing context and the tenancy scope live in context variables that a worker thread would not inherit by default.

What exists instead is a guard for the day it is attempted: `budget.PRIVACY_ORDER` (identity, gate, retrieval, model) is checked
against the order the stages were entered, so a change that starts retrieval or the model before the audience gate fails a test.
