#!/usr/bin/env python3
"""Decide whether the end-to-end jobs may fail the final `CI result` gate (docs/decisions/0013, CONTRIBUTING.md).

On a pull request the browser suite is advisory: its shards still run and show red or green, but a failed or
cancelled one does not fail the gate, unless the pull request carries the `e2e-blocking` label or the label
could not be read (fail closed). On push to main, merge_group, the scheduled Full run and manual runs it is
always blocking.

Standard library only. CLI: EVENT, LABELS ("-" when unreadable) and E2E_RESULTS ("e2e=success e2e-smoke=skipped")
in the environment. Prints `blocking=true|false` and `advisory_failed=N` lines; exit 0 always (the caller decides).
"""

from __future__ import annotations

import os
import sys

BLOCKING_LABEL = "e2e-blocking"
E2E_JOBS = ("e2e", "e2e-smoke")
# A result that is advisory on a pull request. "skipped" is NOT here: a job that should have run and was skipped
# means an upstream problem, which stays fail-closed.
ADVISORY_RESULTS = {"failure", "cancelled"}


def e2e_blocking(event: str, labels: str | None) -> bool:
    """True when a failed e2e job must fail the gate. `labels` is None when they could not be read."""
    if event != "pull_request":
        return True
    if labels is None:
        return True
    return BLOCKING_LABEL in {x.strip() for x in labels.split(",")}


def gate_failures(event: str, labels: str | None, results: dict[str, str]) -> list[str]:
    """The e2e jobs whose result fails the gate (a job that is not success or skipped), advisory ones removed."""
    bad = [j for j in E2E_JOBS if results.get(j, "skipped") not in ("success", "skipped")]
    if e2e_blocking(event, labels):
        return bad
    return [j for j in bad if results[j] not in ADVISORY_RESULTS]


def advisory_failed(event: str, labels: str | None, results: dict[str, str]) -> list[str]:
    """The e2e jobs that failed but are forgiven."""
    bad = [j for j in E2E_JOBS if results.get(j, "skipped") not in ("success", "skipped")]
    return [j for j in bad if j not in gate_failures(event, labels, results)]


def main() -> int:
    event = os.environ.get("EVENT", "")
    raw = os.environ.get("LABELS", "-")
    labels = None if raw == "-" else raw
    results = dict(p.split("=", 1) for p in os.environ.get("E2E_RESULTS", "").split() if "=" in p)
    print(f"blocking={str(e2e_blocking(event, labels)).lower()}")
    print(f"gate_failures={','.join(gate_failures(event, labels, results))}")
    print(f"advisory_failed={','.join(advisory_failed(event, labels, results))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
