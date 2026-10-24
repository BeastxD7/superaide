"""Which channel may say yes to an approval (#125). Pure code, no model, no skill imports.

Moved out of `voice/gateway.py` so `policy.actions` can enforce it without importing the voice package;
`voice.gateway` re-exports these names.
"""

from __future__ import annotations

from superaide.db.models import Approval

# Owner decision (#125): only a tap inside the Superaide app is the second channel. A Telegram or Slack
# tap is not enough, because a chat account is easier to take over than a signed-in session. Fail closed.
TEXT_CHANNELS = ("app",)


def from_voice(approval: Approval) -> bool:
    return (approval.payload or {}).get("origin") == "voice"


def check_confirmation(approval: Approval, via: str) -> str:
    """Why this yes is refused, or "" if it may stand. Called for every approval."""
    if via == "voice":
        return "A spoken yes isn't accepted. Confirm this in the app."
    if from_voice(approval) and via not in TEXT_CHANNELS:
        return "This was asked for on a call. Confirm it in the app."
    return ""
