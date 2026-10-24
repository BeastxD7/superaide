"""Which list a triage decision belongs in: needs you, handled or held."""

from __future__ import annotations

from superaide.triage.models import Escalation, TriageDecision
from superaide.triage.types import Decision, EscalationState

NEEDS_YOU = {Decision.INTERRUPT, Decision.DEFER, Decision.VERIFY}
CLOSED = {EscalationState.RESOLVED, EscalationState.EXPIRED}


def bucket(d: TriageDecision, esc: Escalation | None) -> str:
    """needs_you | handled | held"""
    if d.decision == Decision.HANDLE:
        return "handled"
    maybe = bool((d.features or {}).get("maybe_urgent")) and d.decision == Decision.HOLD
    if (d.decision in NEEDS_YOU or maybe) and d.feedback is None and (esc is None or esc.state not in CLOSED):
        return "needs_you"
    return "held"
