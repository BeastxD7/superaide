"""Two-channel voice (#125): the voice call is a thin shell, and what it proposes is confirmed in the app.

Full-duplex voice agents keep a fraction of text capability on rule-bound tasks and mishear. So the
rules are in code, not in the model:
- The shell holds no tools (`VOICE_TOOLS` is empty; the call already passes `tools=[]`). It turns
  speech into text, hands the intent to the text agent's usual proposal, and reads back what was proposed.
- An action heard on a call is an ordinary approval marked `origin: voice`. It can be approved only
  from the Superaide app (`TEXT_CHANNELS`); a yes that came by voice is refused, and so
  is an approval whose channel is unknown. A no is accepted from anywhere.
- The read-back is the exact proposal as a structured list of fields, ends with where to confirm, and
  says nothing has happened. A spoken reply that claims completion while something is waiting is
  replaced by that read-back.
Nothing here is reachable until VOICE_TWO_CHANNEL is on, and nothing existing changes with it off:
approvals that did not begin on a call are untouched.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from superaide.identity.resolver import Sender
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval
from superaide.policy.actions import decide
from superaide.policy.confirmation import from_voice

VOICE_TOOLS: tuple[()] = ()  # the shell holds no tools
INTERNAL_KEYS = {"summary", "trust_at_request", "required_trust", "origin", "result"}
MAX_FIELD = 120

DONE = re.compile(
    r"""(?ix)\b(?:done|all\s+set|sorted|taken\s+care\s+of|that'?s\s+(?:done|sent|booked)|
        i(?:'ve|\s+have)?\s+(?:just\s+)?(?:sent|booked|scheduled|published|posted|deleted|cancelled|canceled|moved|
        declined|emailed|messaged|forwarded|paid|completed|finished)
        |(?:has|have|is|was|been)\s+(?:been\s+)?(?:sent|booked|scheduled|published|posted|deleted|cancelled|canceled|
        moved|declined|completed))\b"""
)


def propose(
    session: Session,
    *,
    requester: Sender,
    kind: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An action heard on a call. Returns the read-back; the approval waits for a text-channel tap."""
    if not get_settings().voice_two_channel:
        return {"status": "off", "spoken": "I can't take actions by voice yet. Please type it in the app."}
    decision = decide(
        session, kind=kind, summary=summary, requester=requester, payload=payload, origin="voice", require_executor=True
    )
    if decision.unsupported:  # nothing could carry it out (#326): no card, and no pretending on the call
        return {
            "status": "refused",
            "spoken": "I can't do that myself: nothing in Superaide can carry it out yet. I can write the text.",
        }
    if decision.approval is None:
        return {"status": decision.outcome, "spoken": f"I can't do that. {decision.reason.capitalize()}."}
    return {"status": "waiting", "approval_id": str(decision.approval.id), **readback(decision.approval)}


def _label(kind: str) -> str:
    return kind.replace("_", " ")


def readback(approval: Approval) -> dict[str, Any]:
    """The exact proposal as a structured diff, plus the sentence to speak. Never claims it is done."""
    payload = approval.payload or {}
    fields = [
        {"label": str(k).replace("_", " "), "value": _short(v)}
        for k, v in payload.items()
        if k not in INTERNAL_KEYS and v not in (None, "", [], {})
    ]
    summary = str(payload.get("summary", "")).strip()
    spoken = f"I understood: {_label(approval.kind)}. {summary}".strip()
    if not spoken.endswith((".", "!", "?")):
        spoken += "."
    shown = fields[:3]
    for f in shown:
        spoken += f" {f['label'].capitalize()}: {f['value']}."
    if len(fields) > len(shown):
        spoken += f" {len(fields) - len(shown)} more details are on your screen."
    spoken += " I've put it on your screen. Nothing has happened yet; confirm it there."
    return {"kind": approval.kind, "summary": summary, "fields": fields, "spoken": spoken}


def _short(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_FIELD else text[: MAX_FIELD - 1].rstrip() + "…"


def guard_spoken(text: str, waiting: list[Approval]) -> str:
    """What the call may say. While a proposal from the call is waiting, a reply that claims it is
    finished is replaced by the read-back of the newest waiting proposal."""
    pending = [a for a in waiting if a.status == "pending" and from_voice(a)]
    if pending and DONE.search(text):
        return str(readback(pending[0])["spoken"])
    return text
