"""The witness (issue #113): reads the action log and can do exactly one thing, freeze this Ava.

It cannot approve, send, edit or delete. Its only write is `set_frozen`, plus an incident line in the audit
trail and one in-app note to the owner. The gateway already honours the freeze flag before replying or calling
a model, so a frozen Ava stops even if the model, a skill or the executor misbehaves.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from superaide.actionlog import chain
from superaide.platform.audit import audit
from superaide.policy.killswitch import instance_frozen, set_frozen

SIDE_EFFECTS = {"write_external", "send", "spend"}
LOOP_LIMIT = 8  # the same call (tool + arguments) this many times in one log


@dataclass
class Finding:
    rule: str
    seq: int | None
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    frozen: bool = False


def inspect(session: Session) -> list[Finding]:
    found: list[Finding] = []
    verdict = chain.verify(session)
    if not verdict.ok:
        found.append(Finding("chain_broken", verdict.broken_at, verdict.reason))
    rows = chain.entries_of(session)
    for r in rows:
        if r.kind == "executed" and r.effect in {"send", "spend"} and r.approval_id is None:
            found.append(Finding("unapproved_send", r.seq, f"{r.tool} ran with no approval"))
    repeats = Counter((r.tool, r.args_hash) for r in rows if r.effect in SIDE_EFFECTS and r.kind == "call")
    for (tool, _), n in repeats.items():
        if n >= LOOP_LIMIT:
            found.append(Finding("retry_loop", None, f"{tool} was called {n} times with the same arguments"))
    return found


def run(session: Session) -> Report:
    """Look, and freeze if something is wrong. Idempotent: an already frozen Ava is not touched again."""
    report = Report(findings=inspect(session))
    if report.findings and not instance_frozen(session):
        set_frozen(session, True, actor="witness")
        audit(
            session,
            "witness",
            "incident",
            rules=[f.rule for f in report.findings],
            seqs=[f.seq for f in report.findings],
        )
        _tell_owner(session)
        report.frozen = True
    return report


def _tell_owner(session: Session) -> None:
    from superaide.app.accounts import owner
    from superaide.proactive.notify import notify_owner

    person = owner(session)
    if person is not None:
        notify_owner(
            session, person,
            "I stopped myself: a safety check found something odd in my own action log. Open the dashboard to look.",
            {"kind": "witness"}, channel="app",
        )  # fmt: skip
