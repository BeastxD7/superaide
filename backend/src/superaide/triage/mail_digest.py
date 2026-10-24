"""The email part of the owner's digest, and the rule for what an email may be quoted in it.

This used to sit in `skills/gmail_inbox.py` (which reads and triages mail), so `triage.digest` and `triage.routes`
had to import the skill and the skill imported triage back: a cycle (ADR 0015). The digest is triage's output, so it
lives with triage; the skill only produces the decisions.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.db.models import Event
from superaide.triage.buckets import bucket
from superaide.triage.models import TriageDecision

QUOTE_HIDDEN = {"payment", "legal_hr", "credentials"}  # the digest may be pushed to a chat: never quote these
# Mail the digest never quotes, whatever triage decided (it may be pushed to a chat).
NEVER_QUOTED = re.compile(
    r"\b(?:invoice|payment|bank|wire|iban|refund|card|password|passcode|verification|security code|login|"
    r"sign[- ]in|2fa|otp|lawsuit|legal|subpoena|court|attorney|solicitor|contract|termination|disciplinary|"
    r"grievance|tax)\b",
    re.I,
)


def quotable(d: TriageDecision, subject: str) -> bool:
    """Whether an email's subject (or a quote from it) may be shown: never for payment, legal or
    credential mail, judged by the rules, the classifier or the subject itself."""
    return not (
        set((d.classifier or {}).get("risk_flags") or []) & QUOTE_HIDDEN
        or any(h.startswith(("R3_risk:", "R3_classifier:")) for h in d.rule_hits or [])
        or NEVER_QUOTED.search(subject)
    )


def digest_section(session: Session, rows: list[TriageDecision]) -> list[str]:
    """'3 emails need you; 12 handled; 40 newsletters held', then the top items, never quoting
    payment, legal or credential mail."""
    if not rows:
        return []
    events = {e.id: e for e in session.scalars(select(Event).where(Event.id.in_([d.inbound_event_id for d in rows])))}
    need, handled, newsletters, held = [], 0, 0, 0
    for d in rows:
        b = bucket(d, None)
        if b == "needs_you":
            need.append(d)
        elif b == "handled":
            handled += 1
        elif "R4_automated" in (d.rule_hits or []):
            newsletters += 1
        else:
            held += 1
    parts = [f"{len(need)} email{'s' if len(need) != 1 else ''} need{'s' if len(need) == 1 else ''} you"]
    parts.append(f"{handled} handled")
    parts.append(f"{newsletters} newsletter{'s' if newsletters != 1 else ''} held")
    if held:
        parts.append(f"{held} other{'s' if held != 1 else ''} held")
    lines = [f"**Email:** {'; '.join(parts)}"]
    for d in need[:3]:
        meta = (events[d.inbound_event_id].meta or {}) if d.inbound_event_id in events else {}
        who = str(meta.get("from_name") or "Someone")
        subject = str(meta.get("subject") or "(no subject)")
        risky = not quotable(d, subject)
        lines.append(f"- {who}: " + ("an email about money or security (not quoted)" if risky
                                      else f"“{subject[:100]}”"))  # fmt: skip
    if len(need) > 3:
        lines.append(f"- and {len(need) - 3} more in Needs you")
    return lines
