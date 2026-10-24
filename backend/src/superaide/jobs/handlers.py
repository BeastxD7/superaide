"""Import every module that registers a job handler, so workers know all job kinds."""

from __future__ import annotations


def load() -> None:
    import superaide.context.vectors  # noqa: F401  (registers embed_memories)
    import superaide.features.forknight.night  # noqa: F401  (registers fork_night)
    import superaide.features.journal.service  # noqa: F401  (registers journal_from_turn)
    import superaide.intentions.service  # noqa: F401  (registers deliver_intention)
    import superaide.knowledge.connector_facts  # noqa: F401  (registers connector_facts)
    import superaide.knowledge.learn  # noqa: F401  (registers learn_from_turn)
    import superaide.knowledge.pending  # noqa: F401  (registers read_pending)
    import superaide.memory.sleep  # noqa: F401  (registers sleep_consolidate)
    import superaide.proactive.calendar_watch  # noqa: F401  (registers renew_calendar_watch)
    import superaide.proactive.digest  # noqa: F401  (registers send_digest)
    import superaide.proactive.prep  # noqa: F401  (registers meeting_prep, scan_meetings)
    import superaide.skills.gmail_inbox  # noqa: F401  (registers gmail_poll)
    import superaide.triage.buttons  # noqa: F401  (registers escalation_refresh)
    import superaide.triage.digest  # noqa: F401  (registers triage_catchup)
    import superaide.triage.escalate  # noqa: F401  (registers escalation_step)
    import superaide.triage.service  # noqa: F401  (registers triage_inbound)
