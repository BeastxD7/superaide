"""Triage and escalation (ADR 0010): for every message from someone other than the owner,
decide in code whether Ava handles it, holds it for the digest, or interrupts the owner now,
and escalate an interrupt until the owner explicitly acknowledges it.

Behind the `TRIAGE_ENABLED` setting (off by default). Until the pipeline hook (TR5) lands,
triage runs in shadow: decisions are recorded and escalations reach the owner, but what the
sender is told is unchanged.
"""
