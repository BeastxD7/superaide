"""The triage contract: decisions, escalation states, the classifier's strict output, and the
defaults for an owner's triage settings (ADR 0010 sections 3, 5, 7).

Several defaults answer questions the owner hasn't decided yet (ADR 0010, open questions).
They live in `DEFAULTS` and are copied into each owner's `triage_settings` row, so the owner
can change them without a code change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Decision(StrEnum):
    HANDLE = "handle"  # Ava answers within what the owner allowed (approval-gated until TR5/TR6)
    HOLD = "hold"  # kept for the digest
    INTERRUPT = "interrupt"  # tell the owner now and escalate until acknowledged
    DEFER = "defer"  # an interrupt held back to a breakpoint (quiet hours, away, busy, budget)
    VERIFY = "verify"  # a risky request: never handled, shown to the owner as unverified


class Stage(StrEnum):
    INFANT = "infant"
    CHILD = "child"
    ADOLESCENT = "adolescent"
    ADULT = "adult"


STAGE_ORDER = (Stage.INFANT, Stage.CHILD, Stage.ADOLESCENT, Stage.ADULT)


class EscalationState(StrEnum):
    DEFERRED = "deferred"
    NOTIFYING = "notifying"
    ACKED = "acked"
    RESOLVED = "resolved"
    EXPIRED = "expired"


OPEN_STATES = (EscalationState.DEFERRED, EscalationState.NOTIFYING)


class Resolution(StrEnum):
    REPLIED = "replied"
    DONE = "done"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class Feedback(StrEnum):
    USEFUL = "useful"
    NOT_URGENT = "not_urgent"
    SHOULD_HAVE_TOLD = "should_have_told"
    WRONG_HANDLE = "wrong_handle"
    GOOD_HANDLE = "good_handle"


# Buttons (and their in-app equivalents). Opening the app or a read receipt is never one of them.
AckVerb = Literal["ack", "reply", "call", "not_urgent", "snooze_30m", "snooze_2h", "done"]
ACK_SOURCES = ("telegram", "slack", "app", "phone")

Urgency = Literal["low", "normal", "high", "critical"]
URGENCY_RANK: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "critical": 3}
RiskFlag = Literal["payment", "credentials", "secrecy", "impersonation", "instructions", "legal_hr"]


class TriageOutput(BaseModel):
    """What the classifier may say. Fixed labels plus verbatim evidence that code checks;
    it never decides on its own. Numeric ranges are checked here, not in the JSON schema
    (Haiku's structured outputs reject minimum/maximum)."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["question", "request", "scheduling", "fyi", "social", "sales", "other"]
    urgency: Urgency
    deadline_text: str = ""  # verbatim, e.g. "by 5pm today"; code parses it
    needs_owner: bool
    can_answer: Literal["no", "from_notes", "with_action"]
    answer_intent: str = ""  # must be one of the owner's granted intent ids, else ignored
    matched_topics: list[str] = Field(default_factory=list)  # urgent-topic ids ("t1", "t2", …)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # ≤ 3 verbatim quotes from the message
    confidence: float
    reason: str = ""  # shown to the owner only

    @field_validator("confidence")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return v

    @field_validator("evidence")
    @classmethod
    def _few(cls, v: list[str]) -> list[str]:
        if len(v) > 3:
            raise ValueError("at most 3 evidence quotes")
        return v

    @field_validator("reason")
    @classmethod
    def _short(cls, v: str) -> str:
        return v[:200]


# One rung of the ladder. `provider` is "app", "interrupt" (the owner's interruption channel, or
# the first linked one when that is the app) or "other" (the other linked channel). `delay_s` is
# counted from the previous rung; the "interrupt" rung fires at once when the owner chose a chat
# channel for interruptions (ADR 0010 section 5).
DEFAULT_LADDER: list[dict[str, Any]] = [
    {"provider": "app", "delay_s": 0},
    {"provider": "interrupt", "delay_s": 180},
    {"provider": "other", "delay_s": 300},
]

# Proposed defaults (ADR 0010). Items marked "open question" wait for the owner's answer.
DEFAULTS: dict[str, Any] = {
    "ladder": DEFAULT_LADDER,  # open question 3: timings, and which chat channel goes first
    "expire_after_s": 600,  # after the last rung without an ack
    "max_per_hour": 2,  # open question 6
    "max_per_day": 6,  # open question 6
    "vip_ignores_budget": "critical",  # open question 6: "critical" (proposed) | "always" | "never"
    "theta_interrupt": 0.6,
    "theta_handle": 0.7,
    "stranger_intake": False,  # retired: Q1 decided 2026-10-22, see reception/ (receptionist, on by default)
    "away_visibility": "colleagues",  # open question 5: everyone | colleagues | nobody
    "tell_sender_seen": False,  # open question 9 (not used until TR5)
    "phone_enabled": False,  # open question 8 (the phone rung is not built)
}

AWAY_VISIBILITY = ("everyone", "colleagues", "nobody")
POLICY_VERSION = "tr1-2026-10-22"
