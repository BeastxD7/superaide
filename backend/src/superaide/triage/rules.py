"""The hard rules (ADR 0010 section 2), evaluated in code before any model call. The first
rule that decides wins, and every rule that matched is recorded.

    R1 kill switch            handled by the pipeline (nothing happens while frozen)
    R2 silenced sender/topic  hold, never interrupts
    R3 risk screen            verify: never handled autonomously
    R4 automated sender       hold (FYI)
    R5 verified VIP + urgent  interrupt (may break quiet hours)
    R6 repeat breakthrough    an urgent signal for R5/R7, verified senders only
    R7 urgent topic           interrupt if urgency ≥ high, else hold "maybe urgent"
    R8 quiet / away / busy    interrupt → defer          (applied in policy.py)
    R9 interrupt budget       interrupt → defer          (applied in policy.py)

Claimed urgency is not evidence: "URGENT!!" from someone who isn't a VIP, with no topic match,
is held.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from superaide.triage.features import Features
from superaide.triage.types import Decision

# Patterns behind business-email-compromise and social engineering. A match means "verify":
# the owner sees it as unverified and Ava never acts on it alone.
RISK_PATTERNS: dict[str, re.Pattern[str]] = {
    "payment": re.compile(
        r"\b(?:bank|account|payment|wire|routing|iban|swift|sort code)\b.{0,40}\b(?:details?|number|change[ds]?|"
        r"update[ds]?|new)\b|\b(?:change|update|new)\b.{0,30}\b(?:bank|account|payment|iban)\b|"
        r"\bwire (?:transfer|the)\b",
        re.I,
    ),
    "gift_cards": re.compile(r"\bgift ?cards?\b|\bitunes cards?\b|\bsteam cards?\b", re.I),
    "credentials": re.compile(
        r"\b(?:password|passcode|otp|one[- ]time (?:code|password)|verification code|login code|2fa code)\b", re.I
    ),
    "secrecy": re.compile(
        r"\bkeep (?:this|it) (?:between us|quiet|secret|to yourself)\b|\bdon'?t tell\b|\bconfidential(?:ly)?\b.{0,20}"
        r"\bdon'?t\b",
        re.I,
    ),
    "bypass": re.compile(
        r"\bbypass\b|\bskip (?:the )?(?:approval|process|checks?)\b|\bwithout (?:approval|asking)\b", re.I
    ),
    "instructions": re.compile(
        r"\bignore (?:all |any |your |the )?(?:previous |prior )?instructions\b|\bsystem prompt\b|\byou are now\b|"
        r"\bdisregard (?:your|the) rules\b",
        re.I,
    ),
}
DEADLINE_SOON = timedelta(hours=2)
STOP_WORDS = frozenset({"a", "an", "the", "is", "are", "of", "to", "in", "on", "for", "and", "or", "my", "our", "with"})


@dataclass
class RuleResult:
    decision: Decision | None = None  # set when a rule decided outright
    hits: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)  # urgent topics matched by keyword ("t1", …)
    urgent_signal: bool = False  # a code-checked reason to treat this as urgent (topic, deadline, repeat)
    vip_rule: bool = False  # R5 decided (for R8: may break quiet hours)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def topic_matches(topic: str, text: str) -> bool:
    """Every significant word of the topic appears in the message (as a word, or a word's start):
    "server down" matches "the server is down"; "board meeting" matches "board meetings"."""
    want = [w for w in _words(topic) if w not in STOP_WORDS]
    if not want:
        return False
    have = _words(text)
    return all(any(h == w or (len(w) >= 4 and h.startswith(w)) for h in have) for w in want)


def topic_ids(topics: tuple[str, ...], text: str) -> list[str]:
    return [f"t{i + 1}" for i, t in enumerate(topics) if topic_matches(t, text)]


def risk_screen(text: str) -> list[str]:
    return [name for name, pat in RISK_PATTERNS.items() if pat.search(text)]


def evaluate(f: Features) -> RuleResult:
    """The rules that need no model. Leaves `decision` None when the classifier should look."""
    r = RuleResult()
    # R2: the owner silenced this person or topic.
    if f.sender_person_id is not None and f.sender_person_id in f.silenced_people:
        r.hits.append("R2_silenced_person")
    muted = [t for t in f.silenced_topics if topic_matches(t, f.text)]
    if muted:
        r.hits.append("R2_silenced_topic")
    # R3: risk screen.
    r.risks = risk_screen(f.text)
    r.hits += [f"R3_risk:{k}" for k in r.risks]
    # R4: automated or bulk senders.
    if f.automated:
        r.hits.append("R4_automated")
    # Urgent signals checked in code (R6, R7 keyword, a deadline soon).
    r.topic_ids = topic_ids(f.urgent_topics, f.text)
    r.hits += [f"R7_topic:{t}" for t in r.topic_ids]
    if f.verified and f.repeats >= 2:
        r.hits.append("R6_repeat")
    deadline_soon = f.deadline_at is not None and f.deadline_at - f.now <= DEADLINE_SOON
    if deadline_soon:
        r.hits.append("deadline_2h")
    r.urgent_signal = bool(r.topic_ids) or "R6_repeat" in r.hits or deadline_soon

    if "R2_silenced_person" in r.hits or muted:
        r.decision = Decision.HOLD
    elif r.risks:
        r.decision = Decision.VERIFY
    elif f.automated:
        r.decision = Decision.HOLD
    elif f.vip and r.urgent_signal:  # f.vip is already verified-only
        r.hits.append("R5_vip")
        r.decision = Decision.INTERRUPT
        r.vip_rule = True
    return r
