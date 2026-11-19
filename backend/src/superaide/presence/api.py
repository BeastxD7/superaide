"""What other packages may use from presence (ADR 0015)."""

from superaide.presence.models import PresenceState
from superaide.presence.parse import Parsed, parse
from superaide.presence.service import NOTICE, active, clear, lets_through, set_state, status_text

__all__ = ["NOTICE", "Parsed", "PresenceState", "active", "clear", "lets_through", "parse", "set_state", "status_text"]
