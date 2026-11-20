"""What other packages may use from identity (ADR 0015): reading a name claim out of a message, and the owner's
friends routes (#479)."""

from superaide.identity.friends_routes import make_router as make_friends_router
from superaide.identity.linking import detect_claimed_name

__all__ = ["detect_claimed_name", "make_friends_router"]
