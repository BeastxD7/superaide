"""Row-Level Security: Postgres itself keeps each agent instance's rows apart (ADR 0009 T1).

The app's own queries are the first line of defence; this is the second. Every table
with an `agent_id` column gets one policy: a row is visible, and may be written, only
when its `agent_id` equals the transaction's `app.agent_id` setting, which
`db/session.py` sets at the start of every transaction from the instance in scope.
With no setting, the comparison is NULL, so nothing is visible: a forgotten scope
fails closed instead of leaking.

Policies bind only roles that are neither superusers nor BYPASSRLS. The app connects
as such a role (`db/roles.py`); migrations run as the schema owner. FORCE makes the
policy apply to the owner too, so a test or tool connected as owner (but not
superuser) can't read across instances by accident.

A new table with `agent_id` must be added here and enabled in its migration
(`enable(op.get_bind(), ["new_table"])`); tests/test_rls.py fails until it is.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection, text

POLICY = "instance_isolation"
SETTING = "app.agent_id"
TENANT_SETTING = "app.tenant_id"

# Per-instance data: isolated by agent_id.
TABLES: tuple[str, ...] = (
    "agent_state",
    "approval",
    "audit_log",
    "calendar_watch",  # Google Calendar push channels
    "channel_identity",
    "channel_invite",
    "contact",
    "context",
    "document",
    "event",
    "identity_link_event",
    "installed_skill",
    "integration_secret",  # each instance's own credentials (T3)
    "intention",
    "job",
    "link_challenge",
    "llm_provider",
    "memory_embedding",  # outside the ORM metadata (context/vectors.py); only where pgvector exists
    "memory_item",
    "never_ask_topic",  # One Good Question (#121)
    "owner_question",
    "memory_origin",  # independent origins of a belief (#137)
    "memory_influence",  # which notes changed a reply (#122)
    "agreement_log",  # replies that judged the owner (#123)
    "emotional_use",  # relationship safety counter (#124)
    "playbook",  # Skill Ratchet (#120)
    "prompt_version",  # Overnight Tuner (#118)
    "model_role",
    "onboarding",
    "owner_model_price",
    "owner_preference",
    "person",
    "push_send",  # web push (ADR 0011)
    "push_subscription",
    "spend_ledger",
    "escalation",  # triage (ADR 0010)
    "escalation_ack",
    "escalation_delivery",
    "triage_decision",
    "triage_rule",
    "triage_settings",
    "reception_message",  # receptionist (ADR 0010 TR5)
    "reception_settings",
    "ask_link",  # the "Ask my Ava" link
    "ask_request",
    "ask_stat",
)

# Tables that have agent_id but are read before an instance is known, to find it.
EXEMPT: dict[str, str] = {
    "instance_member": "which instances an account belongs to: read at sign-in, before a scope exists",
    "channel_endpoint": "which instance a bot or Slack install belongs to: read to route a message",
    "ask_handle": "which instance a public Ask link belongs to: read to route a visitor; holds no content",
}

_MATCH = f"agent_id = nullif(current_setting('{SETTING}', true), '')::uuid"


def _exists(conn: Connection, table: str) -> bool:
    return bool(conn.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}))


def enable(conn: Connection, tables: Iterable[str] = TABLES) -> list[str]:
    """Turn the policy on for these tables (those that exist). Safe to run again."""
    done = []
    for table in tables:
        if not _exists(conn, table):
            continue
        conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        conn.execute(text(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"'))
        conn.execute(text(f'CREATE POLICY {POLICY} ON "{table}" USING ({_MATCH}) WITH CHECK ({_MATCH})'))
        done.append(table)
    return done


def disable(conn: Connection, tables: Iterable[str] = TABLES) -> None:
    for table in tables:
        if not _exists(conn, table):
            continue
        conn.execute(text(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"'))
        conn.execute(text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))


def set_scope_sql() -> str:
    """Sets both values for the current transaction only (safe with connection pools)."""
    return f"SELECT set_config('{SETTING}', :agent, true), set_config('{TENANT_SETTING}', :tenant, true)"
