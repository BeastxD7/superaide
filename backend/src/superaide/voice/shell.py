"""The live voice shell's one handler (#125): transcript in, text to say out, and no tools.

What the caller said goes to the text agent (`understand`, the usual structured turn). If the agent
proposes an action, it goes through voice/gateway.propose: an approval marked voice-origin that only
a tap inside the Superaide app can confirm, and the spoken reply is the read-back, which says nothing
has happened. Any other reply is passed through the "never say done while it waits" guard.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.brain.turn import TurnOutput
from superaide.db.models import Approval
from superaide.db.session import session_scope
from superaide.identity.resolver import Sender
from superaide.voice import gateway

Understand = Callable[[str], TurnOutput]
RequesterFor = Callable[[Session], Sender]


class VoiceShell:
    tools = gateway.VOICE_TOOLS  # empty, always

    def __init__(self, understand: Understand, requester_for: RequesterFor) -> None:
        self.understand = understand
        self.requester_for = requester_for
        self.proposals: list[dict] = []  # what each action turn proposed, for the caller's screen and the tests

    def handle(self, heard: str) -> str:
        out = self.understand(heard)
        with session_scope() as s:
            if out.requested_action.kind != "none":
                result = gateway.propose(
                    s,
                    requester=self.requester_for(s),
                    kind=out.requested_action.kind,
                    summary=out.requested_action.summary,
                )
                self.proposals.append({**result, "kind": out.requested_action.kind})
                return str(result["spoken"])
            waiting = list(s.scalars(select(Approval).where(Approval.status == "pending")).all())
            return gateway.guard_spoken(out.reply, waiting)
