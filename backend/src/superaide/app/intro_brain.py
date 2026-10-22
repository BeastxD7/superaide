"""Whether the introduction needs to set up Ava's brain first (issue #13), in one place.

The text introduction asks about this up front ("Choose Ava's brain"), and the voice introduction
(VOICE_INTRO_V2) must check the same thing before it starts, so both use these helpers:

- `brain_ready_for_intro(settings)`: Ava has a real model right now (the owner's own key, Superaide AI
  credits they opted in to, or the operator's server key), so she can read and answer straight away.
- `intro_needs_brain_step(settings)`: the introduction should start by asking. False when a model is
  ready, and also when the operator forced offline mode (FAKE_LLM=true): nothing to choose then.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from superaide.config import Settings


def brain_ready_for_intro(settings: Settings) -> bool:
    from superaide.factory import brain_ready

    return brain_ready(settings)


def intro_needs_brain_step(settings: Settings) -> bool:
    return not settings.fake_llm and not brain_ready_for_intro(settings)


def brain_choices(session: Session, settings: Settings) -> dict[str, Any]:
    """What the "Choose Ava's brain" step shows. Never a key."""
    from superaide.brain import platform_ai
    from superaide.factory import brain_status

    status = brain_status(settings)
    credits = platform_ai.view(session) if platform_ai.available() else None
    return {
        "kind": "brain",
        "ready": status.ready,
        "provider": status.provider,
        "credits": credits,
        "models_url": "/dashboard/models",
    }
