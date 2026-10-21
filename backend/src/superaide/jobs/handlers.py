"""Import every module that registers a job handler, so workers know all job kinds."""

from __future__ import annotations


def load() -> None:
    import superaide.intentions.service  # noqa: F401  (registers deliver_intention)
    import superaide.knowledge.learn  # noqa: F401  (registers learn_from_turn)
