"""Import every module that registers a job handler, so workers know all job kinds."""

from __future__ import annotations


def load() -> None:
    import superaide.intentions.service  # noqa: F401  (registers deliver_intention)
