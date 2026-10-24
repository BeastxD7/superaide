"""Reading what the owner shared while no AI was connected (issue #12).

One hook, `brain_became_ready()`, is called from every path that can make the brain ready: connecting
Claude, connecting a provider or choosing its model, opting in to Superaide AI credits, the operator's
own key (picked up at start-up), and a periodic sweep as the safety net. It queues one `read_pending`
job for the instance in scope, and only when documents are waiting and the brain can read them, so it is
safe to call as often as you like.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from superaide.app.accounts import owner as hired_owner
from superaide.jobs.queue import enqueue, handler
from superaide.platform.db.models import Document, Job, Person
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import default_scope, scope_or_default, use_instance

log = logging.getLogger(__name__)

JOB_KIND = "read_pending"
FINISHED = ("done", "failed", "cancelled")


def dedupe_key(owner_id: uuid.UUID) -> str:
    return f"{JOB_KIND}:{owner_id}"


def waiting(session: Session, owner_id: uuid.UUID) -> int:
    """How many of the owner's documents are saved but not read yet."""
    return (
        session.scalar(
            select(func.count(Document.id)).where(
                Document.owner_person_id == owner_id, Document.status == "stored", Document.removed_at.is_(None)
            )
        )
        or 0
    )


def schedule(session: Session, owner_id: uuid.UUID) -> bool:
    """Queue reading for this owner. One job per instance is active at a time: asking again while it is
    queued, running or waiting to retry changes nothing; a finished one is replaced."""
    key = dedupe_key(owner_id)
    session.execute(
        delete(Job).where(Job.agent_id == scope_or_default().agent_id, Job.dedupe_key == key, Job.status.in_(FINISHED))
    )
    return enqueue(session, JOB_KIND, {"owner_id": str(owner_id)}, dedupe_key=key, max_attempts=3) is not None


def brain_became_ready(*, changed: bool = True) -> bool:
    """Call after anything that may have made the brain ready, inside the owner's instance scope.
    Returns whether a reading job was queued. Never raises: enabling AI must not fail because of this,
    and the sweep catches anything missed."""
    from superaide.brain import keyhealth
    from superaide.brain.wiring import brain_ready
    from superaide.vault.store import runtime_settings

    try:
        with session_scope() as s:
            if changed:
                keyhealth.reset(s)  # a key was saved: whatever was refused before may work now
            elif keyhealth.rejected(s):
                return False  # the periodic sweep: don't retry a key that was refused until it changes
            owner = hired_owner(s)
            if owner is None or not waiting(s, owner.id):
                return False
            owner_id = owner.id
        if not brain_ready(runtime_settings()):
            return False
        with session_scope() as s:
            return schedule(s, owner_id)
    except Exception:
        log.exception("couldn't queue reading of the waiting documents")
        return False


def sweep() -> int:
    """The safety net: for every hired instance with documents waiting and a ready brain, queue reading.
    Returns how many jobs it queued."""
    from superaide.platform.tenancy.service import active_scopes

    try:
        with session_scope() as s:
            scopes = active_scopes(s) or [default_scope()]
    except Exception:
        log.exception("couldn't list agent instances")
        return 0
    queued = 0
    for scope in scopes:
        with use_instance(scope):
            queued += brain_became_ready(changed=False)
    return queued


@handler(JOB_KIND)
def read(session: Session, job: Job) -> None:
    from superaide.brain import keyhealth, keynote
    from superaide.brain.wiring import brain_ready, build_llm
    from superaide.knowledge.ingest import read_documents
    from superaide.vault.store import runtime_settings

    settings = runtime_settings()
    if not brain_ready(settings):
        return  # switched off again since it was queued; the next time the brain is ready queues it again
    if keyhealth.rejected(session):
        return  # the key was refused: saving a working one queues this again (brain_became_ready)
    owner_id = uuid.UUID(job.payload["owner_id"])
    agent = SimpleNamespace(settings=settings, llm=build_llm(settings))
    results = read_documents(agent, owner_id)
    if any(not r.read for r in results):
        with session_scope() as s:
            bad = keyhealth.rejected(s)
            if bad:  # not a passing failure: stop retrying and tell the owner once
                owner = s.get(Person, owner_id)
                if owner is not None:
                    keynote.note_owner(s, owner, bad[0], what="your documents stay unread until then")
        if bad:
            return
        raise RuntimeError("couldn't read every document just now")  # the job retries later, with back-off
